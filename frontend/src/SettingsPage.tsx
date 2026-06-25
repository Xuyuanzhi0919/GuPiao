import { useEffect, useState } from "react";
import { Bell, RefreshCw, Send, Settings } from "lucide-react";
import { fetchNotifications, testNotification, updateNotificationConfig } from "./api";
import type { NotificationConfig, NotificationPayload } from "./types";

export function SettingsPage() {
  const [payload, setPayload] = useState<NotificationPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [noticeFilter, setNoticeFilter] = useState("all");
  const [noticeLimit, setNoticeLimit] = useState(12);

  async function refresh() {
    setLoading(true);
    try {
      setPayload(await fetchNotifications(20));
    } finally {
      setLoading(false);
    }
  }

  async function applyConfig(next: Partial<NotificationConfig>) {
    setSaving(true);
    try {
      setPayload(await updateNotificationConfig(next));
    } finally {
      setSaving(false);
    }
  }

  async function sendTest() {
    setSaving(true);
    try {
      setPayload(await testNotification());
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const status = payload?.status;
  const config = status?.config;
  const health = status?.notification_health;
  const notifications = payload?.notifications || [];
  const noticeKinds = [...new Set(notifications.map((item) => item.kind).filter(Boolean))];
  const filteredNotifications = notifications
    .filter((item) => noticeFilter === "all" || (noticeFilter === "failed" ? item.error || !item.sent : item.kind === noticeFilter))
    .sort((left, right) => Number(Boolean(right.error || !right.sent)) - Number(Boolean(left.error || !left.sent)) || right.ts - left.ts);
  const visibleNotifications = filteredNotifications.slice(0, noticeLimit);

  return (
    <main className="settings-page">
      <header className="watch-top">
        <div>
          <h1>配置</h1>
          <nav>
            <a href="/limit-up.html">打板</a>
            <a href="/watch.html">关注</a>
            <a href="/review.html">复盘</a>
            <a className="active" href="/settings.html">配置</a>
            <a href="/diagnostics.html">诊断</a>
          </nav>
        </div>
        <button onClick={refresh} type="button">
          <RefreshCw size={15} />
          刷新
        </button>
      </header>

      <section className="settings-metrics">
        <SettingMetric title="通知" value={status?.enabled ? "开启" : "关闭"} />
        <SettingMetric title="推送通道" value={status?.bark_configured ? `已配置${status.omni_bark_configured ? "全能" : "Bark"}${status.backup_bark_count ? `+${status.backup_bark_count}` : ""}` : "未配置"} />
        <SettingMetric title="冷却" value={status ? `${status.cooldown_sec}s` : "--"} />
        <SettingMetric title="失败重试" value={status?.failed_retry_sec ? `${status.failed_retry_sec}s` : "--"} />
        <SettingMetric title="最近记录" value={status?.recent_count ?? 0} />
      </section>

      <section className="settings-grid">
        <section className="settings-panel">
          <header>
            <h2>
              <Settings size={16} />
              推送规则
            </h2>
            <span>{loading ? "同步中" : saving ? "保存中" : "已同步"}</span>
          </header>
          {config ? (
            <div className="settings-form">
              <ToggleRow label="总开关" checked={config.enabled} disabled={saving} onChange={(enabled) => applyConfig({ enabled })} />
              <ToggleRow label="首次A预警" checked={config.signal_a_enabled} disabled={saving} onChange={(signal_a_enabled) => applyConfig({ signal_a_enabled })} />
              <ToggleRow label="强关注" checked={config.focus_strong_enabled} disabled={saving} onChange={(focus_strong_enabled) => applyConfig({ focus_strong_enabled })} />
              <ToggleRow label="关注股" checked={config.watchlist_signal_enabled} disabled={saving} onChange={(watchlist_signal_enabled) => applyConfig({ watchlist_signal_enabled })} />
              <ToggleRow label="板块共振" checked={config.sector_pulse_enabled} disabled={saving} onChange={(sector_pulse_enabled) => applyConfig({ sector_pulse_enabled })} />
              <ToggleRow label="执行池" checked={config.execution_alert_enabled} disabled={saving} onChange={(execution_alert_enabled) => applyConfig({ execution_alert_enabled })} />
              <ToggleRow label="打板总开关" checked={config.limit_up_signal_enabled} disabled={saving} onChange={(limit_up_signal_enabled) => applyConfig({ limit_up_signal_enabled })} />
              <ToggleRow label="明日核心" checked={config.limit_up_focus_enabled} disabled={saving || !config.limit_up_signal_enabled} onChange={(limit_up_focus_enabled) => applyConfig({ limit_up_focus_enabled })} />
              <ToggleRow label="次日买点" checked={config.next_day_buy_enabled} disabled={saving || !config.limit_up_signal_enabled} onChange={(next_day_buy_enabled) => applyConfig({ next_day_buy_enabled })} />
              <ToggleRow label="剔除票异动" checked={config.next_day_risk_enabled} disabled={saving || !config.limit_up_signal_enabled} onChange={(next_day_risk_enabled) => applyConfig({ next_day_risk_enabled })} />
              <TextRow label="Bark URL" value={config.bark_url || ""} placeholder="https://api.day.app/你的Key" disabled={saving} onApply={(bark_url) => applyConfig({ bark_url })} />
              <TextRow label="备用 Bark" value={config.backup_bark_urls || ""} placeholder="多个备用 URL 用逗号分隔" disabled={saving} onApply={(backup_bark_urls) => applyConfig({ backup_bark_urls })} />
              <TextRow label="全能 Bark Token" value={config.omni_bark_token || ""} placeholder="鸿蒙全能消息推送Bark里的 token" disabled={saving} onApply={(omni_bark_token) => applyConfig({ omni_bark_token })} />
              <TextRow label="全能频道 ID" value={config.omni_bark_channel_id || ""} placeholder="可选，填了则按频道推送" disabled={saving} onApply={(omni_bark_channel_id) => applyConfig({ omni_bark_channel_id })} />
              <TextRow label="全能发送者" value={config.omni_bark_sender || "GuPiao"} placeholder="GuPiao" disabled={saving} onApply={(omni_bark_sender) => applyConfig({ omni_bark_sender })} />
              <TextRow label="全能 API Base" value={config.omni_bark_api_base || "http://www.ggsuper.com.cn/push/api/v1"} placeholder="http://www.ggsuper.com.cn/push/api/v1" disabled={saving} onApply={(omni_bark_api_base) => applyConfig({ omni_bark_api_base })} />
              <TextRow label="强提醒声音" value={config.critical_sound || "alarm"} placeholder="alarm / minuet / bell" disabled={saving} onApply={(critical_sound) => applyConfig({ critical_sound })} />
              <NumberRow label="冷却秒" value={config.cooldown_sec} min={30} max={86400} disabled={saving} onApply={(cooldown_sec) => applyConfig({ cooldown_sec })} />
              <NumberRow label="失败重试秒" value={config.failed_retry_sec || 10} min={3} max={600} disabled={saving} onApply={(failed_retry_sec) => applyConfig({ failed_retry_sec })} />
              <NumberRow label="板块阈值" value={config.sector_pulse_threshold} min={1} max={50} disabled={saving} onApply={(sector_pulse_threshold) => applyConfig({ sector_pulse_threshold })} />
              <button className="settings-test" disabled={saving || !status?.bark_configured} onClick={sendTest} type="button">
                <Send size={14} />
                测试 Bark 推送
              </button>
            </div>
          ) : (
            <div className="empty">等待配置状态</div>
          )}
        </section>

        <section className="settings-panel">
          <header>
            <h2>
              <Bell size={16} />
              最近通知
            </h2>
            <span>{filteredNotifications.length}/{notifications.length} 条</span>
          </header>
          {health ? (
            <div className="notification-health">
              <article>
                <small>成功率</small>
                <strong>{health.sample_count ? `${health.success_rate}%` : "--"}</strong>
              </article>
              <article>
                <small>平均耗时</small>
                <strong>{health.avg_elapsed_ms ? `${health.avg_elapsed_ms}ms` : "--"}</strong>
              </article>
              <article>
                <small>连续失败</small>
                <strong>{health.consecutive_failures}</strong>
              </article>
              <article>
                <small>失败/样本</small>
                <strong>{health.failure_count}/{health.sample_count}</strong>
              </article>
              {health.last_error ? <p>{health.last_error}</p> : null}
            </div>
          ) : null}
          <div className="notice-toolbar">
            <button className={noticeFilter === "all" ? "active" : ""} onClick={() => setNoticeFilter("all")} type="button">
              全部
            </button>
            <button className={noticeFilter === "failed" ? "active danger" : "danger"} onClick={() => setNoticeFilter("failed")} type="button">
              失败
            </button>
            {noticeKinds.map((kind) => (
              <button className={noticeFilter === kind ? "active" : ""} key={kind} onClick={() => setNoticeFilter(kind)} type="button">
                {noticeKindLabel(kind)}
              </button>
            ))}
          </div>
          <div className="settings-list">
            {visibleNotifications.length ? (
              <>
                {visibleNotifications.map((item) => (
                  <article className={item.error || !item.sent ? "failed" : "sent"} key={`${item.ts}-${item.kind}-${item.code}`}>
                    <strong>{item.title}</strong>
                    <span>{new Date(item.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })} · {item.target || item.channel}{item.elapsed_ms ? ` · ${item.elapsed_ms}ms` : ""}{item.sent ? " · 已发送" : " · 仅记录"}</span>
                    <p>{item.error || item.body}</p>
                  </article>
                ))}
                {filteredNotifications.length > noticeLimit ? (
                  <button className="notice-more" onClick={() => setNoticeLimit((value) => value + 12)} type="button">
                    展开更多
                  </button>
                ) : null}
              </>
            ) : (
              <div className="empty">暂无通知</div>
            )}
          </div>
        </section>
      </section>
    </main>
  );
}

function noticeKindLabel(kind: string): string {
  if (kind === "signal-a") return "A异动";
  if (kind === "focus-strong") return "强关注";
  if (kind === "watchlist-signal") return "关注股";
  if (kind === "sector-pulse") return "板块";
  if (kind === "limit-up-focus") return "明日核心";
  if (kind === "next-day-buy") return "次日买点";
  if (kind === "next-day-risk") return "剔除异动";
  if (kind === "limit-up-signal") return "打板";
  return kind;
}

function SettingMetric({ title, value }: { title: string; value: string | number }) {
  return (
    <article>
      <small>{title}</small>
      <strong>{value}</strong>
    </article>
  );
}

function ToggleRow({ label, checked, disabled, onChange }: { label: string; checked: boolean; disabled: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label>
      <span>{label}</span>
      <input checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
    </label>
  );
}

function NumberRow({ label, value, min, max, disabled, onApply }: { label: string; value: number; min: number; max: number; disabled: boolean; onApply: (value: number) => void }) {
  return (
    <label>
      <span>{label}</span>
      <input
        defaultValue={value}
        disabled={disabled}
        key={`${label}-${value}`}
        max={max}
        min={min}
        onBlur={(event) => onApply(Number(event.target.value || value))}
        onKeyDown={(event) => {
          if (event.key === "Enter") onApply(Number(event.currentTarget.value || value));
        }}
        type="number"
      />
    </label>
  );
}

function TextRow({ label, value, placeholder, disabled, onApply }: { label: string; value: string; placeholder: string; disabled: boolean; onApply: (value: string) => void }) {
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  function commit(next = draft) {
    onApply(next.trim());
  }

  return (
    <label>
      <span>{label}</span>
      <input
        value={draft}
        disabled={disabled}
        onBlur={(event) => commit(event.target.value)}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") commit(event.currentTarget.value);
        }}
        placeholder={placeholder}
        type="text"
      />
      <button disabled={disabled || draft.trim() === value.trim()} onClick={() => commit()} type="button">
        保存
      </button>
    </label>
  );
}
