import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import type { User } from "../types";
import { errorMessage } from "../utils/format";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

export function UsersPage({ token, showToast }: Props) {
  const [users, setUsers] = useState<User[]>([]);

  const loadUsers = async () => {
    const data = await api<{ items: User[] }>("/users", token);
    setUsers(data.items);
  };

  useEffect(() => {
    loadUsers().catch((error) => showToast(errorMessage(error), true));
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form).entries());
    const payload: Record<string, unknown> = {
      email: values.email,
      name: values.name,
      role: values.role,
    };
    if (values.password) payload.password = values.password;

    try {
      const data = await api<{ temporary_password?: string }>("/users", token, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await loadUsers();
      showToast(data.temporary_password ? `用户已创建，临时密码：${data.temporary_password}` : "用户已创建");
      form.reset();
    } catch (error) {
      showToast(errorMessage(error), true);
    }
  };

  return (
    <section className="view">
      <div className="topbar">
        <div>
          <h2>用户</h2>
          <p>系统不开放注册，用户由后台创建。</p>
        </div>
        <button onClick={() => loadUsers().catch((error) => showToast(errorMessage(error), true))} type="button">
          刷新
        </button>
      </div>
      <div className="grid two">
        <section className="panel">
          <h3>用户列表</h3>
          <div className="list">
            {users.map((item) => (
              <div className="list-item" key={item.id || item.email}>
                <strong>{item.name}</strong>
                <div className="muted">{item.email}</div>
                <div>
                  {item.role} · {item.status}
                </div>
              </div>
            ))}
          </div>
        </section>
        <section className="panel">
          <h3>添加用户</h3>
          <form className="form-grid single" onSubmit={submit}>
            <label>
              邮箱 <input name="email" type="email" required />
            </label>
            <label>
              名称 <input name="name" required />
            </label>
            <label>
              角色
              <select name="role" defaultValue="maintainer">
                <option value="maintainer">maintainer</option>
                <option value="viewer">viewer</option>
                <option value="admin">admin</option>
              </select>
            </label>
            <label>
              临时密码 <input name="password" placeholder="留空自动生成" />
            </label>
            <button type="submit">创建用户</button>
          </form>
        </section>
      </div>
    </section>
  );
}
