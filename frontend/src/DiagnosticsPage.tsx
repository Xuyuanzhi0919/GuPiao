import { useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { fetchFullHealth } from "./api";

export function DiagnosticsPage() {
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    setLoading(true);
    try {
      setPayload(await fetchFullHealth());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const entries = payload ? Object.entries(payload) : [];

  return (
    <main className="settings-page">
      <header className="watch-top">
        <div>
          <h1>诊断</h1>
          <nav>
            <a href="/limit-up.html">打板</a>
            <a href="/watch.html">关注</a>
            <a href="/review.html">复盘</a>
            <a href="/settings.html">配置</a>
            <a className="active" href="/diagnostics.html">诊断</a>
          </nav>
        </div>
        <button onClick={refresh} type="button">
          <RefreshCw size={15} />
          刷新
        </button>
      </header>

      <section className="settings-panel diagnostics-panel">
        <header>
          <h2>
            <Activity size={16} />
            后端健康状态
          </h2>
          <span>{loading ? "同步中" : "已同步"}</span>
        </header>
        <div className="diagnostics-list">
          {entries.length ? (
            entries.map(([key, value]) => (
              <article key={key}>
                <strong>{key}</strong>
                <pre>{JSON.stringify(value, null, 2)}</pre>
              </article>
            ))
          ) : (
            <div className="empty">等待诊断数据</div>
          )}
        </div>
      </section>
    </main>
  );
}
