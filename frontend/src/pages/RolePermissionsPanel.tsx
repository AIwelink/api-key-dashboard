import { FormEvent, useState } from "react";

import { allNavigationItems, viewLabel } from "../navigation";
import type { RolePermissionsSettings, UserRole, ViewName } from "../types";


type Props = {
  settings: RolePermissionsSettings | null;
  busy: boolean;
  onChange: (settings: RolePermissionsSettings) => void;
  onSave: () => void;
  onCreate: (roleId: string, label: string) => Promise<void>;
  onDelete: (roleId: string) => Promise<void>;
};


export function toggleRoleViewPermission(
  settings: RolePermissionsSettings,
  role: UserRole,
  view: ViewName,
): RolePermissionsSettings {
  if (view === "api-tokens") return settings;
  const entry = settings.roles[role];
  const exists = entry.allowed_views.includes(view);
  const allowedViews = exists
    ? entry.allowed_views.filter((item) => item !== view)
    : [...entry.allowed_views, view];
  const defaultView = entry.default_view && allowedViews.includes(entry.default_view)
    ? entry.default_view
    : allowedViews[0] || null;
  return {
    ...settings,
    roles: {
      ...settings.roles,
      [role]: {
        ...entry,
        allowed_views: allowedViews,
        default_view: defaultView,
      },
    },
  };
}


function setRoleDefaultView(
  settings: RolePermissionsSettings,
  role: UserRole,
  view: ViewName,
): RolePermissionsSettings {
  const entry = settings.roles[role];
  if (!entry.allowed_views.includes(view)) return settings;
  return {
    ...settings,
    roles: {
      ...settings.roles,
      [role]: {
        ...entry,
        default_view: view,
      },
    },
  };
}


function setRoleLabel(
  settings: RolePermissionsSettings,
  role: UserRole,
  label: string,
): RolePermissionsSettings {
  return {
    ...settings,
    roles: {
      ...settings.roles,
      [role]: {
        ...settings.roles[role],
        label,
      },
    },
  };
}


export function RolePermissionsPanel({ settings, busy, onChange, onSave, onCreate, onDelete }: Props) {
  const [showCreate, setShowCreate] = useState(false);
  const [roleId, setRoleId] = useState("");
  const [label, setLabel] = useState("");

  if (!settings) {
    return (
      <section className="panel">
        <div className="muted">正在加载权限配置...</div>
      </section>
    );
  }

  const availableViews = settings.available_views.length
    ? settings.available_views
    : allNavigationItems.map(([view]) => view);

  const closeCreate = () => {
    if (busy) return;
    setShowCreate(false);
    setRoleId("");
    setLabel("");
  };

  const submitCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    try {
      await onCreate(roleId.trim(), label.trim());
      closeCreate();
    } catch {
      // The parent displays the API error and the dialog stays open for correction.
    }
  };

  const deleteRole = async (role: UserRole, roleLabel: string) => {
    if (!window.confirm(`确认删除用户类型“${roleLabel}”吗？`)) return;
    try {
      await onDelete(role);
    } catch {
      // The parent displays the API error and keeps the current settings.
    }
  };

  return (
    <section className="role-permissions-panel">
      <div className="panel-header role-permissions-toolbar">
        <h3>权限管理</h3>
        <div className="button-row">
          <button className="ghost" disabled={busy} onClick={() => setShowCreate(true)} type="button">
            + 添加用户类型
          </button>
          <button className="success-button" disabled={busy} onClick={onSave} type="button">
            {busy ? "保存中..." : "保存权限"}
          </button>
        </div>
      </div>

      <div className="role-permission-grid">
        {settings.role_order.map((role) => {
          const entry = settings.roles[role];
          if (!entry) return null;
          return (
            <article className="role-permission-card" key={role}>
              <div className="role-permission-head">
                <div className="role-permission-identity">
                  <input
                    aria-label={`${role} 显示名称`}
                    className="role-permission-label-input"
                    disabled={busy}
                    maxLength={40}
                    onChange={(event) => onChange(setRoleLabel(settings, role, event.target.value))}
                    value={entry.label}
                  />
                  <code>{role}</code>
                </div>
                <div className="role-permission-actions">
                  <span>{entry.allowed_views.length} 个页面</span>
                  {!entry.builtin && (
                    <button
                      aria-label={`删除${entry.label}`}
                      className="icon-button role-permission-delete"
                      disabled={busy}
                      onClick={() => void deleteRole(role, entry.label)}
                      title={`删除${entry.label}`}
                      type="button"
                    >
                      ×
                    </button>
                  )}
                </div>
              </div>
              <label>
                默认页面
                <select
                  value={entry.default_view || ""}
                  disabled={busy || entry.allowed_views.length === 0}
                  onChange={(event) => onChange(setRoleDefaultView(settings, role, event.target.value as ViewName))}
                >
                  {entry.allowed_views.length ? (
                    entry.allowed_views.map((view) => (
                      <option value={view} key={view}>{viewLabel(view)}</option>
                    ))
                  ) : (
                    <option value="">未设置</option>
                  )}
                </select>
              </label>
              <div className="role-permission-options">
                {availableViews.map((view) => (
                  <label className="checkbox-row" key={`${role}-${view}`}>
                    <input
                      type="checkbox"
                      value={view}
                      checked={entry.allowed_views.includes(view)}
                      disabled={busy || view === "api-tokens"}
                      onChange={() => onChange(toggleRoleViewPermission(settings, role, view))}
                    />
                    <span>{viewLabel(view)}{view === "api-tokens" ? "（仅 owner）" : ""}</span>
                  </label>
                ))}
              </div>
            </article>
          );
        })}
      </div>

      {showCreate && (
        <div className="role-create-backdrop" onMouseDown={closeCreate}>
          <form
            aria-labelledby="role-create-title"
            className="role-create-dialog"
            onMouseDown={(event) => event.stopPropagation()}
            onSubmit={submitCreate}
            role="dialog"
          >
            <div className="panel-header">
              <h3 id="role-create-title">添加用户类型</h3>
              <button className="icon-button" disabled={busy} onClick={closeCreate} title="关闭" type="button">×</button>
            </div>
            <label>
              显示名称
              <input autoFocus maxLength={40} onChange={(event) => setLabel(event.target.value)} required value={label} />
            </label>
            <label>
              英文标识
              <input
                maxLength={32}
                onChange={(event) => setRoleId(event.target.value.toLowerCase())}
                pattern="[a-z][a-z0-9-]{0,31}"
                placeholder="support"
                required
                value={roleId}
              />
            </label>
            <div className="button-row role-create-actions">
              <button disabled={busy} type="submit">{busy ? "添加中..." : "确认添加"}</button>
              <button className="ghost" disabled={busy} onClick={closeCreate} type="button">取消</button>
            </div>
          </form>
        </div>
      )}
    </section>
  );
}
