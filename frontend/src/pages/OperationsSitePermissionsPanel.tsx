import "./OperationsSitePermissionsPanel.css";

import type {
  OperationsSiteId,
  OperationsSitePermissionsSettings,
  UserStatus,
} from "../types";


type Props = {
  settings: OperationsSitePermissionsSettings | null;
  busy: boolean;
  onChange: (settings: OperationsSitePermissionsSettings) => void;
  onSave: () => void;
};


export function toggleOperationsSitePermission(
  settings: OperationsSitePermissionsSettings,
  userId: string,
  siteId: OperationsSiteId,
): OperationsSitePermissionsSettings {
  const target = settings.users.find((user) => user.user_id === userId);
  if (!target) return settings;

  const selected = new Set(target.operations_site_ids);
  if (selected.has(siteId)) selected.delete(siteId);
  else selected.add(siteId);

  const orderedSiteIds = settings.available_sites
    .map((site) => site.id)
    .filter((id) => selected.has(id));

  return {
    ...settings,
    users: settings.users.map((user) => (
      user.user_id === userId
        ? { ...user, operations_site_ids: orderedSiteIds }
        : user
    )),
  };
}


function statusLabel(status?: UserStatus | null) {
  if (status === "active") return "\u6b63\u5e38";
  if (status === "disabled") return "\u5df2\u505c\u7528";
  if (status === "pending_password_reset") return "\u5f85\u91cd\u7f6e\u5bc6\u7801";
  return status || "-";
}


export function OperationsSitePermissionsPanel({
  settings,
  busy,
  onChange,
  onSave,
}: Props) {
  if (!settings) {
    return (
      <section className="operations-site-permissions-panel">
        <div className="muted">{"\u6b63\u5728\u52a0\u8f7d\u8fd0\u8425\u7ad9\u70b9\u6743\u9650..."}</div>
      </section>
    );
  }

  return (
    <section className="operations-site-permissions-panel">
      <div className="panel-header operations-site-permissions-toolbar">
        <h3>{"\u8fd0\u8425\u7ad9\u70b9\u6743\u9650"}</h3>
        <button
          className="success-button"
          disabled={busy}
          onClick={onSave}
          type="button"
        >
          {busy ? "\u4fdd\u5b58\u4e2d..." : "\u4fdd\u5b58\u7ad9\u70b9\u6743\u9650"}
        </button>
      </div>

      <div className="operations-site-permissions-table-wrap">
        <table className="operations-site-permissions-table">
          <thead>
            <tr>
              <th>{"\u7528\u6237"}</th>
              <th>{"\u90ae\u7bb1"}</th>
              <th>{"\u7528\u6237\u7c7b\u578b"}</th>
              <th>{"\u72b6\u6001"}</th>
              {settings.available_sites.map((site) => (
                <th className="operations-site-column" key={site.id}>{site.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {settings.users.map((user) => {
              const identity = user.email || user.name || user.user_id;
              return (
                <tr key={user.user_id}>
                  <td>
                    <strong>{user.name || user.email || user.user_id}</strong>
                    {user.name && <small>{user.user_id}</small>}
                  </td>
                  <td>{user.email || "-"}</td>
                  <td><code>{user.role || "-"}</code></td>
                  <td>
                    <span className={"operations-user-status " + (user.status || "")}>
                      {statusLabel(user.status)}
                    </span>
                  </td>
                  {settings.available_sites.map((site) => (
                    <td className="operations-site-column" key={site.id}>
                      <input
                        aria-label={identity + " " + site.label}
                        checked={user.operations_site_ids.includes(site.id)}
                        disabled={busy}
                        onChange={() => onChange(toggleOperationsSitePermission(settings, user.user_id, site.id))}
                        type="checkbox"
                      />
                    </td>
                  ))}
                </tr>
              );
            })}
            {!settings.users.length && (
              <tr>
                <td className="operations-site-permissions-empty" colSpan={4 + settings.available_sites.length}>
                  {"\u6682\u65e0\u540e\u53f0\u7528\u6237"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
