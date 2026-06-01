import { useEffect, useState } from "react";
import logoUrl from "../AIwelink_logo_bule_A.png";
import { AccountPoolsPage } from "./pages/AccountPoolsPage";
import { AccountsPage } from "./pages/AccountsPage";
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
  ["pool-lifecycle", "账号池逻辑管理"],
];

const adminNavItems: Array<[ViewName, string]> = [
  ["agent-analysis", "Agent分析"],
  ["api-tokens", "系统 Token"],
  ["users", "用户管理"],
  ["logs", "日志"],
];

function App() {
  const [token, setToken] = useState(() => localStorage.getItem("token") || "");
  const [user, setUser] = useState<User | null>(() => {
    const raw = localStorage.getItem("user");
    return raw ? (JSON.parse(raw) as User) : null;
  });
  const [view, setView] = useState<ViewName>("upload");
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

  useEffect(() => {
    const handleAuthExpired = () => {
      setToken("");
      setUser(null);
      setView("upload");
      localStorage.removeItem("token");
      localStorage.removeItem("user");
      showToast("登录过期", true);
    };

    window.addEventListener("auth-expired", handleAuthExpired);
    return () => window.removeEventListener("auth-expired", handleAuthExpired);
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-logo" src={logoUrl} alt="AIwelink" />
          <div>
            <h1>AIwelink</h1>
            <p>sub2api 账号管理</p>
          </div>
        </div>

        <nav className="nav">
          {[navItems, accountNavItems, poolNavItems, adminNavItems].map((group, index) => (
            <div className="nav-group" key={index}>
              {group.map(([key, label]) => (
                <button
                  className={`nav-item ${view === key ? "active" : ""}`}
                  disabled={!token}
                  key={key}
                  onClick={() => setView(key)}
                  type="button"
                >
                  {label}
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="session-box">
          <div>{user ? `${user.name || user.email} (${user.role})` : "未登录"}</div>
          {token && (
            <button className="ghost" onClick={logout} type="button">
              退出
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
              setView("upload");
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
