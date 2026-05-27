import { FormEvent } from "react";
import { api } from "../api/client";
import type { User } from "../types";
import { errorMessage } from "../utils/format";

type Props = {
  onLogin: (token: string, user: User) => void;
  showToast: (message: string, isError?: boolean) => void;
};

export function LoginPage({ onLogin, showToast }: Props) {
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget).entries());
    try {
      const data = await api<{ access_token: string; user: User }>("/auth/login", "", {
        method: "POST",
        body: JSON.stringify(values),
      });
      onLogin(data.access_token, data.user);
    } catch (error) {
      showToast(errorMessage(error), true);
    }
  };

  return (
    <section className="panel auth-panel">
      <div className="panel-header">
        <h2>登录</h2>
        <p>使用后台创建的账号登录。</p>
      </div>
      <form className="form-grid single" onSubmit={submit}>
        <label>
          邮箱
          <input name="email" type="email" autoComplete="username" required />
        </label>
        <label>
          密码
          <input name="password" type="password" autoComplete="current-password" required />
        </label>
        <button type="submit">登录</button>
      </form>
    </section>
  );
}
