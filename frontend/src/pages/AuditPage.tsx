import { useEffect, useState } from "react";
import { api } from "../api/client";
import { usePageAutoRefresh } from "../hooks/usePageAutoRefresh";
import { errorMessage, pretty } from "../utils/format";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

export function AuditPage({ token, showToast }: Props) {
  const [output, setOutput] = useState<unknown>(null);

  const loadAudit = async () => {
    const data = await api<unknown>("/audit-logs", token);
    setOutput(data);
  };

  usePageAutoRefresh(loadAudit);

  useEffect(() => {
    loadAudit().catch((error) => showToast(errorMessage(error), true));
  }, []);

  return (
    <section className="view">
      <div className="topbar">
        <div>
          <h2>日志</h2>
          <p>当前先展示数据库审计日志。系统运行日志已写入后端 logs 目录，后续再做页面化查询。</p>
        </div>
        <button onClick={() => loadAudit().catch((error) => showToast(errorMessage(error), true))} type="button">
          刷新
        </button>
      </div>
      <section className="panel">
        <pre className="output">{output === null ? "" : pretty(output)}</pre>
      </section>
    </section>
  );
}
