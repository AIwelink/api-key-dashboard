import type { UserPermissions, ViewName } from "./types";


const navItems: Array<[ViewName, string]> = [
  ["upload", "上传账号"],
  ["todos", "代办与错误账号处理"],
];

const accountNavItems: Array<[ViewName, string]> = [
  ["push-error-todos", "疑问账号分配面板"],
  ["accounts", "账号列表"],
];

const poolNavItems: Array<[ViewName, string]> = [
  ["available-pool", "可用池"],
  ["reserve-pool", "使用备选池"],
  ["api-pools", "API 账号池状态"],
  ["plus-self-produced", "plus自产"],
];

const poolOperationsNavItems: Array<[ViewName, string]> = [
  ["event-records", "事件记录"],
  ["alert-center", "异常告警"],
  ["pool-lifecycle", "账号池管理"],
  ["client-sites", "客户站点"],
  ["traffic-analysis-config", "访问流量分析配置"],
];

const operationsManagementNavItems: Array<[ViewName, string]> = [
  ["traffic-analysis", "访问流量分析"],
  ["operations-management", "运营管理"],
];

const adminNavItems: Array<[ViewName, string]> = [
  ["agent-analysis", "Agent分析"],
  ["agent-workbench", "Agent工作台"],
  ["system-management", "系统管理"],
  ["presence", "前台在线"],
  ["users", "用户管理"],
  ["logs", "日志"],
];

const navigationGroups = [
  navItems,
  accountNavItems,
  poolNavItems,
  operationsManagementNavItems,
  poolOperationsNavItems,
  adminNavItems,
];

export const hiddenNavItems = new Set<ViewName>([
  "upload",
  "todos",
  "push-error-todos",
  "accounts",
  "available-pool",
  "reserve-pool",
]);

export const allNavigationItems: Array<[ViewName, string]> = Array.from(
  new Map(navigationGroups.flat().map(([key, label]) => [key, label])).entries(),
);

export const navShortLabels: Record<ViewName, string> = {
  upload: "传",
  todos: "办",
  "push-error-todos": "疑",
  accounts: "账",
  "available-pool": "可",
  "reserve-pool": "备",
  "api-pools": "池",
  "plus-self-produced": "产",
  "traffic-analysis": "流",
  "operations-management": "运",
  "event-records": "事",
  "alert-center": "警",
  "pool-lifecycle": "站",
  "client-sites": "客",
  "traffic-analysis-config": "配",
  "agent-analysis": "析",
  "agent-workbench": "台",
  "system-management": "管",
  "api-tokens": "管",
  presence: "在",
  users: "用",
  logs: "志",
};

export const viewPaths: Record<ViewName, string> = {
  upload: "/upload-accounts",
  todos: "/todo-and-error-accounts",
  "push-error-todos": "/question-account-assignment",
  accounts: "/accounts",
  "available-pool": "/available-pool",
  "reserve-pool": "/reserve-pool",
  "api-pools": "/api-pool-status",
  "plus-self-produced": "/plus-self-produced",
  "traffic-analysis": "/traffic-analysis",
  "operations-management": "/operations-management",
  "event-records": "/event-records",
  "alert-center": "/alert-center",
  "pool-lifecycle": "/account-pool-management",
  "client-sites": "/client-sites",
  "traffic-analysis-config": "/traffic-analysis-config",
  "agent-analysis": "/agent-analysis",
  "agent-workbench": "/agent-workbench",
  "system-management": "/system-management",
  "api-tokens": "/system-management",
  presence: "/user-presence",
  users: "/users",
  logs: "/logs",
};

const pathAliases: Record<string, ViewName> = {
  "/upload": "upload",
  "/todos": "todos",
  "/push-error-todos": "push-error-todos",
  "/api-pools": "api-pools",
  "/api-tokens": "system-management",
  "/pool-lifecycle": "pool-lifecycle",
  "/site-configuration": "pool-lifecycle",
};

export function defaultViewForLayout(): ViewName {
  return "api-pools";
}

export function canAccessView(permissions: UserPermissions | null | undefined, view: ViewName) {
  return Boolean(permissions?.allowed_views.includes(view));
}

export function defaultViewForPermissions(permissions?: UserPermissions | null): ViewName {
  if (permissions?.default_view === "api-tokens" && canAccessView(permissions, "system-management")) {
    return "system-management";
  }
  if (permissions?.default_view && canAccessView(permissions, permissions.default_view)) return permissions.default_view;
  return permissions?.allowed_views.find((view) => !hiddenNavItems.has(view)) || permissions?.allowed_views[0] || defaultViewForLayout();
}

export function getVisibleNavigationGroups(permissions?: UserPermissions | null): Array<Array<[ViewName, string]>> {
  return navigationGroups
    .map((group) => group.filter(([key]) => canAccessView(permissions, key) && !hiddenNavItems.has(key)))
    .filter((group) => group.length > 0);
}

export function viewLabel(view: ViewName) {
  return allNavigationItems.find(([key]) => key === view)?.[1] || view;
}

export function viewFromPath(pathname: string): ViewName {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  if (normalized === "/") return defaultViewForLayout();
  const matched = Object.entries(viewPaths).find(([, path]) => path === normalized);
  return matched ? (matched[0] as ViewName) : pathAliases[normalized] || defaultViewForLayout();
}

export function navigationGroupClass(group: Array<[ViewName, string]>) {
  const firstKey = group[0]?.[0];
  if (firstKey === "api-pools") return "nav-group pool-status-nav-group";
  if (firstKey === "traffic-analysis") return "nav-group operations-management-nav-group";
  if (firstKey === "event-records") return "nav-group pool-operations-nav-group";
  return "nav-group";
}
