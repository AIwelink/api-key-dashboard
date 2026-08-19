import { useEffect, useRef, useState } from "react";
import { LoaderCircle, LogOut, QrCode, ShieldCheck } from "lucide-react";
import logoUrl from "../../AIwelink_logo_bule_A.png";
import { api } from "../api/client";
import {
  launchFeishuAuthorization,
  openFeishuPopup,
  readStoredFeishuSession,
  startFeishuSessionPolling,
  type FeishuAuthPhase,
  type FeishuAuthorizationSession,
} from "../auth/feishu";
import type { User } from "../types";
import { errorMessage } from "../utils/format";

type Props = {
  token: string;
  user: User;
  onBound: (token: string, user: User) => void;
  onLogout: () => void;
};

const PHASE_COPY: Record<FeishuAuthPhase, string> = {
  idle: "使用飞书扫码确认当前账号",
  starting: "正在创建安全绑定会话",
  waiting: "请在飞书中确认授权",
  exchanging: "授权完成，正在更新账号",
  binding: "请在飞书中确认授权",
  failed: "绑定未完成，请重新扫码",
};

export function FeishuBindingPage({ token, user, onBound, onLogout }: Props) {
  const [phase, setPhase] = useState<FeishuAuthPhase>("idle");
  const [inlineError, setInlineError] = useState("");
  const stopPolling = useRef<(() => void) | null>(null);
  const busy = phase === "starting" || phase === "waiting" || phase === "exchanging" || phase === "binding";

  const beginPolling = (
    session: FeishuAuthorizationSession,
    popup: ReturnType<typeof openFeishuPopup> = null,
  ) => {
    stopPolling.current?.();
    stopPolling.current = startFeishuSessionPolling(
      { ...session, flow: "binding" },
      {
        onLogin: onBound,
        onPhase: (nextPhase) => setPhase(nextPhase === "waiting" ? "binding" : nextPhase),
        onError: (message) => {
          setInlineError(message);
          setPhase("failed");
        },
      },
      { popup },
    );
  };

  useEffect(() => {
    const stored = readStoredFeishuSession();
    const callbackSessionId = new URLSearchParams(window.location.search).get("feishu_session");
    if (stored?.flow === "binding" && callbackSessionId === stored.session_id) {
      setPhase("binding");
      window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.hash}`);
      beginPolling(stored);
    }
    return () => stopPolling.current?.();
  }, []);

  const startBinding = async () => {
    const popup = openFeishuPopup();
    setPhase("starting");
    setInlineError("");
    try {
      const session = await api<FeishuAuthorizationSession>("/auth/feishu/bind-session", token, { method: "POST" });
      setPhase("binding");
      const mode = launchFeishuAuthorization(session, { popup, flow: "binding" });
      if (mode === "popup") beginPolling(session, popup);
    } catch (error) {
      if (popup && !popup.closed) popup.close();
      setInlineError(errorMessage(error));
      setPhase("failed");
    }
  };

  return (
    <main className="pending-auth-page feishu-binding-page">
      <div className="pending-auth-brand">
        <img src={logoUrl} alt="AIwelink" />
        <strong>AIwelink</strong>
      </div>
      <section className="pending-auth-panel feishu-binding-panel" aria-labelledby="feishu-binding-title">
        <div className={`pending-auth-avatar feishu-binding-mark ${busy ? "is-scanning" : ""}`} aria-hidden="true">
          {phase === "exchanging" ? <LoaderCircle className="auth-spinner" /> : <QrCode />}
          <span />
        </div>
        <span className="pending-auth-label">企业身份确认</span>
        <h1 id="feishu-binding-title">绑定飞书后才能继续使用</h1>
        <p>{user.name || user.email || "当前账号"} 已登录，请完成飞书扫码绑定。</p>
        <div className={`auth-state is-active feishu-binding-state`} data-auth-phase={phase} role="status">
          <span className="auth-state-mark" aria-hidden="true"><ShieldCheck /></span>
          <span>
            <strong>{PHASE_COPY[phase]}</strong>
            <small>绑定后可直接使用飞书扫码登录</small>
          </span>
        </div>
        {inlineError ? <p className="auth-inline-error" role="alert">{inlineError}</p> : null}
        <div className="pending-auth-actions feishu-binding-actions">
          <button className="feishu-login-button" data-action="feishu-bind" disabled={busy} onClick={startBinding} type="button">
            {busy ? <LoaderCircle className="auth-spinner" aria-hidden="true" /> : <QrCode aria-hidden="true" />}
            {busy ? PHASE_COPY[phase] : "立即绑定飞书"}
          </button>
          <button className="ghost" onClick={onLogout} type="button">
            <LogOut aria-hidden="true" />
            退出登录
          </button>
        </div>
      </section>
    </main>
  );
}
