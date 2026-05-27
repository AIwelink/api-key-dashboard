import { useEffect, useState } from "react";
import { api } from "../api/client";
import { errorMessage, pretty } from "../utils/format";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

export function SyncPage({ token, showToast }: Props) {
  const [output, setOutput] = useState<unknown>(null);

  const loadJobs = async () => {
    const data = await api<unknown>("/sync/jobs", token);
    setOutput(data);
  };

  useEffect(() => {
    loadJobs().catch((error) => showToast(errorMessage(error), true));
  }, []);

  const runSync = async (dryRun: boolean) => {
    try {
      const data = await api<unknown>(dryRun ? "/sync/preview" : "/sync/run", token, {
        method: "POST",
        body: JSON.stringify({ dry_run: dryRun }),
      });
      setOutput(data);
      if (!dryRun) showToast("同步完成");
    } catch (error) {
      showToast(errorMessage(error), true);
    }
  };

  return (
    <section className="view">
      <div className="topbar">
        <div>
          <h2>同步</h2>
          <p>当前为占位同步，会刷新 metadata 中的观测字段。</p>
        </div>
        <div className="button-row">
          <button onClick={() => runSync(true)} type="button">
            预览同步
          </button>
          <button onClick={() => runSync(false)} type="button">
            执行同步
          </button>
        </div>
      </div>
      <section className="panel">
        <div className="panel-header">
          <h3>同步任务</h3>
          <button onClick={() => loadJobs().catch((error) => showToast(errorMessage(error), true))} type="button">
            刷新任务
          </button>
        </div>
        <pre className="output">{output === null ? "" : pretty(output)}</pre>
      </section>
    </section>
  );
}
