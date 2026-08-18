import { LogOut, RefreshCw, ShieldCheck } from "lucide-react";
import logoUrl from "../../AIwelink_logo_bule_A.png";
import type { User } from "../types";

type Props = {
  user: User;
  refreshing: boolean;
  onRefresh: () => void;
  onLogout: () => void;
};

export function PendingAuthorizationPage({ user, refreshing, onRefresh, onLogout }: Props) {
  return (
    <main className="pending-auth-page">
      <div className="pending-auth-brand">
        <img src={logoUrl} alt="AIwelink" />
        <strong>AIwelink</strong>
      </div>
      <section className="pending-auth-panel" aria-labelledby="pending-auth-title">
        <div className="pending-auth-avatar" aria-hidden={!user.feishu_avatar_url}>
          {user.feishu_avatar_url
            ? <img src={user.feishu_avatar_url} alt="" />
            : <ShieldCheck aria-hidden="true" />}
        </div>
        <span className="pending-auth-label">飞书身份已确认</span>
        <h1 id="pending-auth-title">尚未分配系统权限，请联系管理员</h1>
        <p>{user.feishu_name || user.name || user.email}</p>
        <div className="pending-auth-actions">
          <button disabled={refreshing} onClick={onRefresh} type="button">
            <RefreshCw aria-hidden="true" className={refreshing ? "auth-spinner" : ""} />
            {refreshing ? "正在刷新" : "刷新权限"}
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
