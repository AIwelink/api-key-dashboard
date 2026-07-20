import { useEffect, useState } from "react";
import logoUrl from "../AIwelink_logo_bule_A.png";
import { AccountPoolsPage } from "./pages/AccountPoolsPage";
import { AccountsPage } from "./pages/AccountsPage";
import { AgentAnalysisPage } from "./pages/AgentAnalysisPage";
import { AgentWorkbenchPage } from "./pages/AgentWorkbenchPage";
import { AlertCenterPage } from "./pages/AlertCenterPage";
import { ApiPoolStatusPage } from "./pages/ApiPoolStatusPage";
import { ApiTokensPage } from "./pages/ApiTokensPage";
import { AuditPage } from "./pages/AuditPage";
import { ClientSitesPage } from "./pages/ClientSitesPage";
import { EventRecordsPage } from "./pages/EventRecordsPage";
import { IntroPage } from "./pages/IntroPage";
import { AvailablePoolPage, ReservePoolPage } from "./pages/ManualPoolPage";
import { PushErrorTodoPage, TodoPage } from "./pages/TodoPage";
import { LoginPage } from "./pages/LoginPage";
import { PresencePage } from "./pages/PresencePage";
import { UploadPage } from "./pages/UploadPage";
import { UsersPage } from "./pages/UsersPage";
import { useForegroundPresence } from "./hooks/useForegroundPresence";
import type { User, ViewName } from "./types";

type ToastState = {
  message: string;
  isError: boolean;
} | null;

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
  ["event-records", "事件记录"],
  ["alert-center", "异常告警"],
  ["pool-lifecycle", "账号池管理"],
  ["client-sites", "客户站点"],
];

const adminNavItems: Array<[ViewName, string]> = [
  ["agent-analysis", "Agent分析"],
  ["agent-workbench", "Agent工作台"],
  ["api-tokens", "系统管理"],
  ["presence", "前台在线"],
  ["users", "用户管理"],
  ["logs", "日志"],
];

const hiddenNavItems = new Set<ViewName>([
  "upload",
  "todos",
  "push-error-todos",
  "accounts",
  "available-pool",
  "reserve-pool",
]);

export function getVisibleNavigationGroups(canViewPresence: boolean): Array<Array<[ViewName, string]>> {
  return [
    navItems,
    accountNavItems,
    poolNavItems,
    adminNavItems.filter(([key]) => key !== "presence" || canViewPresence),
  ]
    .map((group) => group.filter(([key]) => !hiddenNavItems.has(key)))
    .filter((group) => group.length > 0);
}

const navShortLabels: Record<ViewName, string> = {
  upload: "传",
  todos: "办",
  "push-error-todos": "疑",
  accounts: "账",
  "available-pool": "可",
  "reserve-pool": "备",
  "api-pools": "池",
  "event-records": "事",
  "alert-center": "警",
  "pool-lifecycle": "站",
  "client-sites": "客",
  "agent-analysis": "析",
  "agent-workbench": "台",
  "api-tokens": "管",
  presence: "在",
  users: "用",
  logs: "志",
};

const viewPaths: Record<ViewName, string> = {
  upload: "/upload-accounts",
  todos: "/todo-and-error-accounts",
  "push-error-todos": "/question-account-assignment",
  accounts: "/accounts",
  "available-pool": "/available-pool",
  "reserve-pool": "/reserve-pool",
  "api-pools": "/api-pool-status",
  "event-records": "/event-records",
  "alert-center": "/alert-center",
  "pool-lifecycle": "/account-pool-management",
  "client-sites": "/client-sites",
  "agent-analysis": "/agent-analysis",
  "agent-workbench": "/agent-workbench",
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
  "/api-tokens": "api-tokens",
  "/pool-lifecycle": "pool-lifecycle",
  "/site-configuration": "pool-lifecycle",
};

function isMobileMenuLayout() {
  return window.matchMedia("(max-width: 720px), (max-width: 900px) and (orientation: portrait), (max-aspect-ratio: 3 / 4)").matches;
}

function defaultViewForLayout(): ViewName {
  return "api-pools";
}

export function viewFromPath(pathname: string): ViewName {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  if (normalized === "/") return defaultViewForLayout();
  const matched = Object.entries(viewPaths).find(([, path]) => path === normalized);
  return matched ? (matched[0] as ViewName) : pathAliases[normalized] || defaultViewForLayout();
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem("token") || "");
  const [user, setUser] = useState<User | null>(() => {
    const raw = localStorage.getItem("user");
    return raw ? (JSON.parse(raw) as User) : null;
  });
  const [view, setView] = useState<ViewName>(() => viewFromPath(window.location.pathname));
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem("sidebarCollapsed") === "true");
  const [toast, setToast] = useState<ToastState>(null);
  useForegroundPresence(token, view);

  const showToast = (message: string, isError = false) => {
    setToast({ message, isError });
    window.setTimeout(() => setToast(null), 3200);
  };

  const logout = () => {
    setToken("");
    setUser(null);
    localStorage.removeItem("token");
    localStorage.removeItem("user");
  };

  const navigateToView = (nextView: ViewName) => {
    setView(nextView);
    const nextPath = viewPaths[nextView];
    if (window.location.pathname !== nextPath) {
      window.history.pushState({ view: nextView }, "", nextPath);
    }
    if (isMobileMenuLayout()) {
      setSidebarCollapsed(true);
      localStorage.setItem("sidebarCollapsed", "true");
    }
  };

  const toggleSidebar = () => {
    setSidebarCollapsed((current) => {
      const next = !current;
      localStorage.setItem("sidebarCollapsed", String(next));
      return next;
    });
  };

  useEffect(() => {
    const handlePopState = () => {
      setView(viewFromPath(window.location.pathname));
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    if (token && user && view === "presence" && user.role !== "owner") {
      navigateToView(defaultViewForLayout());
    }
  }, [token, user, view]);

  useEffect(() => {
    const handleAuthExpired = () => {
      setToken("");
      setUser(null);
      navigateToView("api-pools");
      localStorage.removeItem("token");
      localStorage.removeItem("user");
      showToast("登录过期", true);
    };

    window.addEventListener("auth-expired", handleAuthExpired);
    return () => window.removeEventListener("auth-expired", handleAuthExpired);
  }, []);

  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-logo" src={logoUrl} alt="AIwelink" />
          <div className="brand-copy">
            <h1>AIwelink</h1>
            <p>API 客户端管理</p>
          </div>
        </div>
        <button className="sidebar-toggle" onClick={toggleSidebar} title={sidebarCollapsed ? "展开菜单" : "收起菜单"} type="button">
          {sidebarCollapsed ? "›" : "‹"}
        </button>

        <nav className="nav">
          {getVisibleNavigationGroups(user?.role === "owner").map((group, index) => (
            <div className="nav-group" key={index}>
              {group.map(([key, label]) => (
                <button
                  className={`nav-item ${view === key ? "active" : ""}`}
                  disabled={!token}
                  key={key}
                  onClick={() => navigateToView(key)}
                  title={label}
                  type="button"
                >
                  <span className="nav-short">{navShortLabels[key]}</span>
                  <span className="nav-label">{label}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="session-box">
          <div className="session-user">{user ? `${user.name || user.email} (${user.role})` : "未登录"}</div>
          {token && (
            <button className="ghost" onClick={logout} type="button">
              <span className="session-logout-full">退出</span>
              <span className="session-logout-short">退</span>
            </button>
          )}
        </div>
      </aside>

      <main className="main">
        {!token ? (
          <LoginPage
            onLogin={(nextToken, nextUser) => {
              setToken(nextToken);
              setUser(nextUser);
              localStorage.setItem("token", nextToken);
              localStorage.setItem("user", JSON.stringify(nextUser));
              if (window.location.pathname === "/") navigateToView(defaultViewForLayout());
              showToast("登录成功");
            }}
            showToast={showToast}
          />
        ) : (
          <>
            {view === "upload" && <UploadPage token={token} showToast={showToast} />}
            {view === "todos" && <TodoPage token={token} showToast={showToast} />}
            {view === "push-error-todos" && <PushErrorTodoPage token={token} showToast={showToast} />}
            {view === "accounts" && <AccountsPage token={token} showToast={showToast} />}
            {view === "available-pool" && <AvailablePoolPage token={token} showToast={showToast} />}
            {view === "reserve-pool" && <ReservePoolPage token={token} showToast={showToast} />}
            {view === "api-pools" && <ApiPoolStatusPage token={token} showToast={showToast} />}
            {view === "event-records" && <EventRecordsPage token={token} showToast={showToast} />}
            {view === "alert-center" && <AlertCenterPage token={token} showToast={showToast} />}
            {view === "pool-lifecycle" && <AccountPoolsPage token={token} showToast={showToast} />}
            {view === "client-sites" && <ClientSitesPage token={token} showToast={showToast} />}
            {view === "agent-analysis" && <AgentAnalysisPage token={token} showToast={showToast} />}
            {view === "agent-workbench" && <AgentWorkbenchPage token={token} showToast={showToast} />}
            {view === "api-tokens" && <ApiTokensPage token={token} showToast={showToast} />}
            {view === "presence" && user?.role === "owner" && <PresencePage token={token} showToast={showToast} />}
            {view === "users" && <UsersPage token={token} showToast={showToast} />}
            {view === "logs" && <AuditPage token={token} showToast={showToast} />}
          </>
        )}
        {toast && <div className={`toast ${toast.isError ? "danger" : ""}`}>{toast.message}</div>}
      </main>
    </div>
  );
}

export default App;
