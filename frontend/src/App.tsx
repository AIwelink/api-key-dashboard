import { useEffect, useState } from "react";
import logoUrl from "../AIwelink_logo_bule_A.png";
import { AccountPoolsPage } from "./pages/AccountPoolsPage";
import { AccountsPage } from "./pages/AccountsPage";
import { AlertCenterPage } from "./pages/AlertCenterPage";
import { ApiPoolStatusPage } from "./pages/ApiPoolStatusPage";
import { ApiTokensPage } from "./pages/ApiTokensPage";
import { AuditPage } from "./pages/AuditPage";
import { IntroPage } from "./pages/IntroPage";
import { AvailablePoolPage, ReservePoolPage } from "./pages/ManualPoolPage";
import { PushErrorTodoPage, TodoPage } from "./pages/TodoPage";
import { LoginPage } from "./pages/LoginPage";
import { UploadPage } from "./pages/UploadPage";
import { UsersPage } from "./pages/UsersPage";
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
  ["alert-center", "异常告警"],
  ["pool-lifecycle", "账号池管理"],
];

const adminNavItems: Array<[ViewName, string]> = [
  ["agent-analysis", "Agent分析"],
  ["api-tokens", "系统管理"],
  ["users", "用户管理"],
  ["logs", "日志"],
];

const navShortLabels: Record<ViewName, string> = {
  upload: "传",
  todos: "办",
  "push-error-todos": "疑",
  accounts: "账",
  "available-pool": "可",
  "reserve-pool": "备",
  "api-pools": "池",
  "alert-center": "警",
  "pool-lifecycle": "逻",
  "agent-analysis": "析",
  "api-tokens": "管",
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
  "alert-center": "/alert-center",
  "pool-lifecycle": "/pool-lifecycle",
  "agent-analysis": "/agent-analysis",
  "api-tokens": "/system-management",
  users: "/users",
  logs: "/logs",
};

const pathAliases: Record<string, ViewName> = {
  "/upload": "upload",
  "/todos": "todos",
  "/push-error-todos": "push-error-todos",
  "/api-pools": "api-pools",
  "/api-tokens": "api-tokens",
};

function isMobileMenuLayout() {
  return window.matchMedia("(max-width: 720px), (max-width: 900px) and (orientation: portrait), (max-aspect-ratio: 3 / 4)").matches;
}

function defaultViewForLayout(): ViewName {
  return isMobileMenuLayout() ? "api-pools" : "upload";
}

function viewFromPath(pathname: string): ViewName {
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
    const handleAuthExpired = () => {
      setToken("");
      setUser(null);
      navigateToView("upload");
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
            <p>sub2api 账号管理</p>
          </div>
        </div>
        <button className="sidebar-toggle" onClick={toggleSidebar} title={sidebarCollapsed ? "展开菜单" : "收起菜单"} type="button">
          {sidebarCollapsed ? "›" : "‹"}
        </button>

        <nav className="nav">
          {[navItems, accountNavItems, poolNavItems, adminNavItems].map((group, index) => (
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
            {view === "alert-center" && <AlertCenterPage token={token} showToast={showToast} />}
            {view === "pool-lifecycle" && <AccountPoolsPage token={token} showToast={showToast} />}
            {view === "agent-analysis" && (
              <IntroPage
                title="Agent分析"
                description="这里后续用于辅助判断制作新账号、renew 旧账号、处理问题账号和调整池策略。当前先保留介绍页，不执行自动决策。"
                points={[
                  "Agent 只做辅助分析，不绕过本地手动流程和安全规则。",
                  "后续会读取账号状态、问题账号、容量指标和历史错误，给出建议。",
                  "真正执行动作仍需要人工确认，或走后端明确的状态机和审计记录。",
                ]}
              />
            )}
            {view === "api-tokens" && <ApiTokensPage token={token} showToast={showToast} />}
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
