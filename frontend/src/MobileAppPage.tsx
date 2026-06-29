import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Bell, CircleDot, Flame, RefreshCw, Settings, ShieldAlert, Target, Wallet, Wifi, WifiOff } from "lucide-react";
import { fetchLimitUpNextDayMonitor, fetchLimitUpTomorrowFocus, fetchMarketQuotes, fetchNotifications, fetchPositions, getApiBase, limitUpWebSocketUrl, setApiBase } from "./api";
import { formatMoney, formatPct } from "./format";
import type { LimitUpNextDayPayload, LimitUpNextDayRow, LimitUpStock, LimitUpTomorrowFocusPayload, NotificationPayload, Position } from "./types";

type MobileTab = "buy" | "hold" | "pool" | "limit" | "focus" | "notice" | "settings";
type StreamState = "connecting" | "live" | "stale";

export function MobileAppPage() {
  const [monitor, setMonitor] = useState<LimitUpNextDayPayload | null>(null);
  const [focus, setFocus] = useState<LimitUpTomorrowFocusPayload | null>(null);
  const [notifications, setNotifications] = useState<NotificationPayload | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [quotes, setQuotes] = useState<Record<string, { price?: number; change_pct?: number; open?: number; high?: number; low?: number }>>({});
  const [tab, setTab] = useState<MobileTab>("buy");
  const [stream, setStream] = useState<StreamState>("connecting");
  const [status, setStatus] = useState("");
  const [backend, setBackend] = useState(getApiBase());

  async function load(silent = false) {
    if (!silent) setStatus("同步中...");
    try {
      const [nextMonitor, nextFocus, nextNotifications] = await Promise.all([
        fetchLimitUpNextDayMonitor(false),
        fetchLimitUpTomorrowFocus(false),
        fetchNotifications(30),
      ]);
      const nextPositions = await fetchPositions();
      const positionRows = nextPositions.positions || [];
      const quotePayload = positionRows.length ? await fetchMarketQuotes(positionRows.map((item) => item.code)) : { quotes: {} };
      setMonitor(nextMonitor);
      setFocus(nextFocus);
      setNotifications(nextNotifications);
      setPositions(positionRows);
      setQuotes(quotePayload.quotes || {});
      if (!silent) setStatus("已同步");
    } catch (error) {
      if (!silent) setStatus(error instanceof Error ? error.message : "同步失败");
    }
  }

  useEffect(() => {
    load(false);
  }, []);

  useEffect(() => {
    let closed = false;
    let retryTimer = 0;
    let socket: WebSocket | null = null;

    function connect() {
      setStream("connecting");
      try {
        socket = new WebSocket(limitUpWebSocketUrl());
      } catch {
        setStream("stale");
        retryTimer = window.setTimeout(connect, 2500);
        return;
      }
      socket.onopen = () => setStream("live");
      socket.onmessage = (event) => {
        try {
          setMonitor(JSON.parse(event.data) as LimitUpNextDayPayload);
          setStream("live");
        } catch {
          setStream("stale");
        }
      };
      socket.onerror = () => setStream("stale");
      socket.onclose = () => {
        if (closed) return;
        setStream("stale");
        retryTimer = window.setTimeout(connect, 2500);
      };
    }

    connect();
    return () => {
      closed = true;
      window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [backend]);

  const official = monitor?.buy_signals || [];
  const opportunities = useMemo(() => (monitor?.opportunity_signals || monitor?.rows || []).filter((item) => item.action !== "PASS").slice(0, 20), [monitor]);
  const todayPool = monitor?.today_pool || [];
  const watchPool = monitor?.watch_pool || [];
  const coreFocus = (focus?.focus || []).filter((item) => item.openclaw_tier === "core").slice(0, 12);
  const health = notifications?.status.notification_health;

  return (
    <main className="app-mobile-shell">
      <header className="app-mobile-top">
        <div>
          <span>{monitor?.date || "--"} · 打板 App</span>
          <h1>正式买点 {monitor?.summary.buy_signal_count ?? 0}/3</h1>
        </div>
        <button onClick={() => load(false)} type="button">
          <RefreshCw size={17} />
        </button>
      </header>

      <section className="app-mobile-status">
        <b className={stream === "live" ? "live" : stream === "connecting" ? "connecting" : "stale"}>
          {stream === "live" ? <Wifi size={14} /> : <WifiOff size={14} />}
          {stream === "live" ? "实时流" : stream === "connecting" ? "连接中" : "重连中"}
        </b>
        <span>{monitor?.phase?.label || status || "等待数据"}</span>
        <span>剩余 {monitor?.summary.remaining_buy_slots ?? 3}</span>
      </section>

      {tab === "buy" ? (
        <MobilePanel empty="暂无正式买点" items={official} render={(item, index) => <MobileBuyCard item={item} rank={index + 1} />} />
      ) : null}
      {tab === "hold" ? (
        <section className="app-mobile-list">
          <h2>当前持仓 <small>{positions.length}</small></h2>
          {positions.length ? positions.map((item) => <MobilePositionCard item={item} key={item.code} quote={quotes[item.code]} />) : <p className="empty">暂无持仓</p>}
        </section>
      ) : null}
      {tab === "pool" ? (
        <MobilePanel empty="暂无机会池" items={opportunities} render={(item) => <MobileBuyCard item={item} />} />
      ) : null}
      {tab === "limit" ? (
        <section className="app-mobile-list">
          <h2>今日涨停池 <small>{todayPool.length}</small></h2>
          {todayPool.length ? todayPool.slice(0, 40).map((item) => <MobileStockCard item={item} key={item.code} />) : <p className="empty">暂无今日涨停</p>}
          <h2>昨日涨停池 <small>{watchPool.length}</small></h2>
          {watchPool.slice(0, 20).map((item) => <MobileStockCard item={item} key={`watch-${item.code}`} />)}
        </section>
      ) : null}
      {tab === "focus" ? (
        <section className="app-mobile-list">
          <h2>明日核心 <small>{coreFocus.length || focus?.summary.focus_count || 0}</small></h2>
          {(coreFocus.length ? coreFocus : focus?.focus || []).slice(0, 30).map((item) => <MobileStockCard item={item} key={item.code} />)}
        </section>
      ) : null}
      {tab === "notice" ? (
        <section className="app-mobile-list">
          <h2>推送健康</h2>
          <div className="app-health-grid">
            <article><small>成功率</small><b>{health?.sample_count ? `${health.success_rate}%` : "--"}</b></article>
            <article><small>耗时</small><b>{health?.avg_elapsed_ms ? `${health.avg_elapsed_ms}ms` : "--"}</b></article>
            <article><small>连续失败</small><b>{health?.consecutive_failures ?? 0}</b></article>
          </div>
          {(notifications?.notifications || []).slice(0, 20).map((item) => (
            <article className={item.sent ? "notice sent" : "notice failed"} key={`${item.ts}-${item.kind}-${item.code}`}>
              <b>{item.title}</b>
              <span>{item.target || item.channel} · {new Date(item.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })}</span>
              <p>{item.error || item.body}</p>
            </article>
          ))}
        </section>
      ) : null}
      {tab === "settings" ? (
        <section className="app-mobile-list">
          <h2>App 设置</h2>
          <label className="app-backend-field">
            <span>后端地址</span>
            <input placeholder="http://你的Mac地址:8788" value={backend} onChange={(event) => setBackend(event.target.value)} />
          </label>
          <button
            className="app-primary-action"
            onClick={() => {
              const saved = setApiBase(backend);
              setBackend(saved);
              load(false);
            }}
            type="button"
          >
            保存并重连
          </button>
          <p className="app-hint">同一 Wi-Fi 可填 Mac 局域网地址；外网建议用 Tailscale/ZeroTier 的私有地址。</p>
        </section>
      ) : null}

      <nav className="app-mobile-tabs">
        <TabButton active={tab === "buy"} icon={<Target size={18} />} label="买点" onClick={() => setTab("buy")} />
        <TabButton active={tab === "hold"} icon={<Wallet size={18} />} label="持仓" onClick={() => setTab("hold")} />
        <TabButton active={tab === "pool"} icon={<CircleDot size={18} />} label="机会" onClick={() => setTab("pool")} />
        <TabButton active={tab === "limit"} icon={<Flame size={18} />} label="涨停" onClick={() => setTab("limit")} />
        <TabButton active={tab === "focus"} icon={<ShieldAlert size={18} />} label="重点" onClick={() => setTab("focus")} />
        <TabButton active={tab === "notice"} icon={<Bell size={18} />} label="推送" onClick={() => setTab("notice")} />
        <TabButton active={tab === "settings"} icon={<Settings size={18} />} label="设置" onClick={() => setTab("settings")} />
      </nav>
    </main>
  );
}

function MobilePanel({ empty, items, render }: { empty: string; items: LimitUpNextDayRow[]; render: (item: LimitUpNextDayRow, index: number) => ReactNode }) {
  return <section className="app-mobile-list">{items.length ? items.map((item, index) => <div key={item.code}>{render(item, index)}</div>) : <p className="empty">{empty}</p>}</section>;
}

function MobileBuyCard({ item, rank }: { item: LimitUpNextDayRow; rank?: number }) {
  return (
    <article className={`app-buy-card ${item.official_buy ? "official" : ""}`}>
      <header>
        <b>{rank ? `正式#${rank}` : item.state}</b>
        <span>{item.score?.toFixed(0) || "--"}分</span>
      </header>
      <h2>{item.name}<small>{item.code}</small></h2>
      <p>{item.sector} · {item.state} · 涨幅{formatPct(item.change_pct)} · 成交{formatMoney(item.amount)}</p>
      <div>{item.reasons.slice(0, 5).map((reason) => <i key={reason}>{reason}</i>)}</div>
    </article>
  );
}

function MobilePositionCard({ item, quote }: { item: Position; quote?: { price?: number; change_pct?: number; open?: number; high?: number; low?: number } }) {
  const price = Number(quote?.price || item.buy_price || 0);
  const pnlPct = item.buy_price > 0 ? (price / item.buy_price - 1) * 100 : 0;
  const pnlAmount = (price - item.buy_price) * item.shares;
  return (
    <article className="app-position-card">
      <header>
        <b>{item.name}<small>{item.code}</small></b>
        <em className={pnlPct > 0 ? "up" : pnlPct < 0 ? "down" : "flat"}>{formatPct(pnlPct)}</em>
      </header>
      <p>{item.sector} · {item.source === "limit-up" ? "打板持仓" : "手动持仓"} · {item.buy_date || "--"}</p>
      <div>
        <span><small>成本</small><strong>{item.buy_price.toFixed(2)}</strong></span>
        <span><small>现价</small><strong>{price ? price.toFixed(2) : "--"}</strong></span>
        <span><small>股数</small><strong>{item.shares}</strong></span>
        <span><small>盈亏</small><strong className={pnlAmount > 0 ? "up" : pnlAmount < 0 ? "down" : "flat"}>{formatMoney(pnlAmount)}</strong></span>
      </div>
    </article>
  );
}

function MobileStockCard({ item }: { item: LimitUpStock }) {
  return (
    <article className="app-stock-card">
      <b>{item.name}<small>{item.code}</small></b>
      <span>{item.sector || "未分组"} · {item.streak || 1}板 · 首封{item.first_limit_time || "--"}</span>
      <em>{item.openclaw_score ? `AI ${item.openclaw_score}` : item.focus_score ? `${item.focus_score.toFixed(0)}分` : formatMoney(item.seal_amount || 0)}</em>
    </article>
  );
}

function TabButton({ active, icon, label, onClick }: { active: boolean; icon: ReactNode; label: string; onClick: () => void }) {
  return <button className={active ? "active" : ""} onClick={onClick} type="button">{icon}<span>{label}</span></button>;
}
