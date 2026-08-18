import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import logoUrl from "../AIwelink_logo_bule_A.png";
import { DailyTeamIntroGate } from "./components/dailyIntro/DailyTeamIntro";
import { AccountPoolsPage } from "./pages/AccountPoolsPage";
import { AccountsPage } from "./pages/AccountsPage";
import { AgentAnalysisPage } from "./pages/AgentAnalysisPage";
import { AgentWorkbenchPage } from "./pages/AgentWorkbenchPage";
import { AlertCenterPage } from "./pages/AlertCenterPage";
import { ApiPoolStatusPage } from "./pages/ApiPoolStatusPage";
import { ApiTokensPage } from "./pages/ApiTokensPage";
import { AuditPage } from "./pages/AuditPage";
import { AutoReplenishmentPage } from "./pages/AutoReplenishmentPage";
import { ClientSitesPage } from "./pages/ClientSitesPage";
import { EventRecordsPage } from "./pages/EventRecordsPage";
import { AvailablePoolPage, ReservePoolPage } from "./pages/ManualPoolPage";
import { PushErrorTodoPage, TodoPage } from "./pages/TodoPage";
import { LoginPage } from "./pages/LoginPage";
import { OperationsManagementPage } from "./pages/OperationsManagementPage";
import { PresencePage } from "./pages/PresencePage";
import { PlusSelfProducedPage } from "./pages/PlusSelfProducedPage";
import { PendingAuthorizationPage } from "./pages/PendingAuthorizationPage";
import { TrafficAnalysisPage } from "./pages/TrafficAnalysisPage";
import { TrafficAnalysisConfigPage } from "./pages/TrafficAnalysisConfigPage";
import { UploadPage } from "./pages/UploadPage";
import { UsersPage } from "./pages/UsersPage";
import { WorkPlansPage } from "./pages/WorkPlansPage";
import { useForegroundPresence } from "./hooks/useForegroundPresence";
import {
  canAccessView,
  defaultViewForPermissions,
  getVisibleNavigationGroups,
  navigationGroupClass,
  navShortLabels,
  viewFromPath,
  viewPaths,
} from "./navigation";
import { api } from "./api/client";
import type { User, ViewName } from "./types";
import { errorMessage } from "./utils/format";

type ToastState = {
  message: string;
  isError: boolean;
} | null;

export function useStableToast(setToast: Dispatch<SetStateAction<ToastState>>) {
  const timerRef = useRef<number | null>(null);
  const showToast = useCallback((message: string, isError = false) => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    setToast({ message, isError });
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      setToast(null);
    }, 3_200);
  }, [setToast]);

  useEffect(() => () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  return showToast;
}

function isMobileMenuLayout() {
  return window.matchMedia("(max-width: 720px), (max-width: 900px) and (orientation: portrait), (max-aspect-ratio: 3 / 4)").matches;
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
  const [refreshingAuthorization, setRefreshingAuthorization] = useState(false);
  const showToast = useStableToast(setToast);
  const pendingAuthorization = Boolean(token && user?.authorization_status === "pending");
  useForegroundPresence(pendingAuthorization ? "" : token, view);

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

  const completeLogin = (nextToken: string, nextUser: User) => {
    setToken(nextToken);
    setUser(nextUser);
    localStorage.setItem("token", nextToken);
    localStorage.setItem("user", JSON.stringify(nextUser));
    if (nextUser.authorization_status !== "pending" && window.location.pathname === "/") {
      navigateToView(defaultViewForPermissions(nextUser.permissions));
    }
    showToast("登录成功");
  };

  const refreshAuthorization = async () => {
    if (!token) return;
    setRefreshingAuthorization(true);
    try {
      const nextUser = await api<User>("/auth/me", token);
      setUser(nextUser);
      localStorage.setItem("user", JSON.stringify(nextUser));
      if (nextUser.authorization_status === "pending") {
        showToast("权限暂未更新，请联系管理员");
      } else {
        if (window.location.pathname === "/") navigateToView(defaultViewForPermissions(nextUser.permissions));
        showToast("权限已更新");
      }
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setRefreshingAuthorization(false);
    }
  };

  const toggleSidebar = () => {
    setSidebarCollapsed((current) => {
      const next = !current;
      localStorage.setItem("sidebarCollapsed", String(next));
      return next;
    });
  };
  const permissions = user?.permissions;
  const canRenderCurrentView = Boolean(token && permissions && canAccessView(permissions, view));

  useEffect(() => {
    const handlePopState = () => {
      setView(viewFromPath(window.location.pathname));
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    if (token && user?.authorization_status !== "pending" && permissions && !canAccessView(permissions, view)) {
      navigateToView(defaultViewForPermissions(permissions));
    }
  }, [token, user?.authorization_status, permissions, view]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    api<User>("/auth/me", token)
      .then((nextUser) => {
        if (cancelled) return;
        setUser(nextUser);
        localStorage.setItem("user", JSON.stringify(nextUser));
      })
      .catch((error) => {
        if (!cancelled) showToast(errorMessage(error), true);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

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

  if (!token) {
    return (
      <>
        <DailyTeamIntroGate user={user} />
        <main className="auth-shell site-motion-scope">
          <LoginPage onLogin={completeLogin} showToast={showToast} />
          {toast && <div className={`toast ${toast.isError ? "danger" : ""}`}>{toast.message}</div>}
        </main>
      </>
    );
  }

  if (!user) {
    return (
      <>
        <DailyTeamIntroGate user={user} />
        <main className="auth-loading-page" role="status">正在确认登录状态</main>
      </>
    );
  }

  if (pendingAuthorization) {
    return (
      <>
        <DailyTeamIntroGate user={user} />
        <PendingAuthorizationPage
          user={user}
          refreshing={refreshingAuthorization}
          onRefresh={() => { void refreshAuthorization(); }}
          onLogout={logout}
        />
        {toast && <div className={`toast ${toast.isError ? "danger" : ""}`}>{toast.message}</div>}
      </>
    );
  }

  return (
    <>
      <DailyTeamIntroGate user={user} />
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
          {getVisibleNavigationGroups(permissions).map((group, index) => (
            <div className={navigationGroupClass(group)} key={index}>
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
        <div
          className="app-view-stage site-motion-scope"
          data-view={token ? view : "login"}
          key={token ? view : "login"}
        >
          {canRenderCurrentView ? (
          <>
            {view === "work-plans" && user && <WorkPlansPage currentUser={user} token={token} showToast={showToast} />}
            {view === "upload" && <UploadPage token={token} showToast={showToast} />}
            {view === "todos" && <TodoPage token={token} showToast={showToast} />}
            {view === "push-error-todos" && <PushErrorTodoPage token={token} showToast={showToast} />}
            {view === "accounts" && <AccountsPage token={token} showToast={showToast} />}
            {view === "available-pool" && <AvailablePoolPage token={token} showToast={showToast} />}
            {view === "reserve-pool" && <ReservePoolPage token={token} showToast={showToast} />}
            {view === "api-pools" && <ApiPoolStatusPage token={token} showToast={showToast} />}
            {view === "plus-self-produced" && <PlusSelfProducedPage token={token} showToast={showToast} />}
            {view === "traffic-analysis" && <TrafficAnalysisPage token={token} showToast={showToast} />}
            {view === "operations-management" && (
              <OperationsManagementPage
                key={(user?.operations_site_ids || []).join("|")}
                token={token}
                role={user?.role || "viewer"}
                allowedSiteIds={user?.operations_site_ids || []}
                showToast={showToast}
              />
            )}
            {view === "event-records" && <EventRecordsPage token={token} showToast={showToast} />}
            {view === "alert-center" && <AlertCenterPage token={token} showToast={showToast} />}
            {view === "pool-lifecycle" && <AccountPoolsPage token={token} showToast={showToast} />}
            {view === "auto-replenishment" && <AutoReplenishmentPage token={token} showToast={showToast} />}
            {view === "client-sites" && <ClientSitesPage token={token} showToast={showToast} />}
            {view === "traffic-analysis-config" && <TrafficAnalysisConfigPage token={token} showToast={showToast} />}
            {view === "agent-analysis" && <AgentAnalysisPage token={token} showToast={showToast} />}
            {view === "agent-workbench" && <AgentWorkbenchPage token={token} showToast={showToast} />}
            {view === "system-management" && (
              <ApiTokensPage
                canManageApiTokens={canAccessView(permissions, "api-tokens")}
                token={token}
                showToast={showToast}
              />
            )}
            {view === "presence" && <PresencePage token={token} showToast={showToast} />}
            {view === "users" && (
              <UsersPage
                canManageOwners={user?.role === "owner"}
                token={token}
                showToast={showToast}
              />
            )}
            {view === "logs" && <AuditPage token={token} showToast={showToast} />}
          </>
          ) : null}
        </div>
        {toast && <div className={`toast ${toast.isError ? "danger" : ""}`}>{toast.message}</div>}
      </main>
      </div>
    </>
  );
}

export default App;
