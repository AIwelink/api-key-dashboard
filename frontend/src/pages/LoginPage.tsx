import { FormEvent, useEffect, useRef, useState } from "react";
import { ChevronDown, LoaderCircle, QrCode, ShieldCheck } from "lucide-react";
import logoUrl from "../../AIwelink_logo_bule_A.png";
import { api } from "../api/client";
import {
  isBindingRequired,
  launchFeishuAuthorization,
  openFeishuPopup,
  readStoredFeishuSession,
  startFeishuSessionPolling,
  type FeishuAuthFlow,
  type FeishuAuthPhase,
  type FeishuAuthorizationSession,
} from "../auth/feishu";
import type { AuthLoginResponse, User } from "../types";
import { errorMessage } from "../utils/format";

type Props = {
  onLogin: (token: string, user: User) => void;
  showToast: (message: string, isError?: boolean) => void;
};

const PHASE_COPY: Record<FeishuAuthPhase, { title: string; detail: string }> = {
  idle: { title: "使用飞书登录", detail: "打开飞书完成企业身份验证" },
  starting: { title: "正在连接飞书", detail: "授权窗口即将打开" },
  waiting: { title: "等待飞书授权", detail: "请在新窗口中确认登录" },
  exchanging: { title: "正在确认身份", detail: "授权已完成，正在进入系统" },
  binding: { title: "完成飞书绑定", detail: "密码已验证，请在飞书中确认当前账号" },
  failed: { title: "本次授权未完成", detail: "请重新发起飞书扫码登录" },
};

export function LoginPage({ onLogin, showToast }: Props) {
  const [phase, setPhase] = useState<FeishuAuthPhase>("idle");
  const [flow, setFlow] = useState<FeishuAuthFlow>("login");
  const [inlineError, setInlineError] = useState("");
  const stopPolling = useRef<(() => void) | null>(null);
  const copy = PHASE_COPY[phase];
  const busy = phase === "starting" || phase === "waiting" || phase === "exchanging" || phase === "binding";

  const beginPolling = (
    session: FeishuAuthorizationSession,
    nextFlow: FeishuAuthFlow,
    popup: ReturnType<typeof openFeishuPopup> = null,
  ) => {
    stopPolling.current?.();
    stopPolling.current = startFeishuSessionPolling(
      { ...session, flow: nextFlow },
      {
        onLogin,
        onPhase: (nextPhase) => setPhase(nextFlow === "binding" && nextPhase === "waiting" ? "binding" : nextPhase),
        onError: (message) => {
          setInlineError(message);
          showToast(message, true);
        },
      },
      { popup },
    );
  };

  const continueWithSession = (
    session: FeishuAuthorizationSession,
    nextFlow: FeishuAuthFlow,
    popup: ReturnType<typeof openFeishuPopup>,
  ) => {
    setFlow(nextFlow);
    setInlineError("");
    const mode = launchFeishuAuthorization(session, { popup, flow: nextFlow });
    if (mode === "popup") beginPolling(session, nextFlow, popup);
  };

  useEffect(() => {
    const stored = readStoredFeishuSession();
    const callbackSessionId = new URLSearchParams(window.location.search).get("feishu_session");
    if (stored && callbackSessionId === stored.session_id) {
      const restoredFlow = stored.flow || "login";
      setFlow(restoredFlow);
      setPhase(restoredFlow === "binding" ? "binding" : "waiting");
      window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.hash}`);
      beginPolling(stored, restoredFlow);
    }
    return () => stopPolling.current?.();
  }, []);

  const startFeishuLogin = async () => {
    const popup = openFeishuPopup();
    setPhase("starting");
    setFlow("login");
    setInlineError("");
    try {
      const session = await api<FeishuAuthorizationSession>("/auth/feishu/sessions", "", { method: "POST" });
      setPhase("waiting");
      continueWithSession(session, "login", popup);
    } catch (error) {
      if (popup && !popup.closed) popup.close();
      const message = errorMessage(error);
      setPhase("failed");
      setInlineError(message);
      showToast(message, true);
    }
  };

  const submitPassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget).entries());
    setPhase("starting");
    setInlineError("");
    try {
      const data = await api<AuthLoginResponse>("/auth/login", "", {
        method: "POST",
        body: JSON.stringify(values),
      });
      if (!isBindingRequired(data)) {
        onLogin(data.access_token, data.user);
        return;
      }
      setPhase("binding");
      continueWithSession(data, "binding", openFeishuPopup());
    } catch (error) {
      const message = errorMessage(error);
      setPhase("failed");
      setInlineError(message);
      showToast(message, true);
    }
  };

  return (
    <section className="auth-experience" aria-labelledby="auth-title">
      <div className="auth-brand-lockup">
        <img src={logoUrl} alt="AIwelink" />
        <div>
          <strong>AIwelink</strong>
          <span>团队协作平台</span>
        </div>
      </div>

      <section className="auth-panel">
        <header className="auth-heading">
          <span className="auth-kicker">企业账号入口</span>
          <h1 id="auth-title">登录 AIwelink</h1>
        </header>

        <button
          className="feishu-login-button"
          data-action="feishu-login"
          disabled={busy}
          onClick={startFeishuLogin}
          type="button"
        >
          {busy && flow === "login" ? <LoaderCircle aria-hidden="true" className="auth-spinner" /> : <QrCode aria-hidden="true" />}
          <span>{busy && flow === "login" ? copy.title : "飞书扫码登录"}</span>
        </button>

        <div className={`auth-state ${phase !== "idle" ? "is-active" : ""}`} data-auth-phase={phase} role="status">
          <span className="auth-state-mark" aria-hidden="true">
            {phase === "exchanging" ? <LoaderCircle className="auth-spinner" /> : <ShieldCheck />}
          </span>
          <span>
            <strong>{copy.title}</strong>
            <small>{copy.detail}</small>
          </span>
        </div>

        {inlineError && <p className="auth-inline-error" role="alert">{inlineError}</p>}

        {!(flow === "binding" && busy) && (
          <details className="password-login">
            <summary>
              <span>账号密码登录</span>
              <ChevronDown aria-hidden="true" />
            </summary>
            <form className="form-grid single" onSubmit={submitPassword}>
              <label>
                邮箱
                <input name="email" type="email" autoComplete="username" required />
              </label>
              <label>
                密码
                <input name="password" type="password" autoComplete="current-password" required />
              </label>
              <button className="ghost auth-password-submit" disabled={busy} type="submit">验证账号</button>
            </form>
          </details>
        )}
      </section>

      <p className="auth-footnote">飞书身份仅用于登录与团队账号绑定</p>
    </section>
  );
}
