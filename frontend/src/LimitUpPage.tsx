import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowDownWideNarrow, Bell, CalendarClock, ChevronDown, ChevronUp, Flame, RefreshCw, Search, ShieldAlert, Target, TrendingUp } from "lucide-react";
import { fetchLimitUpNextDayMonitor, fetchLimitUpOpenClawReviewStatus, fetchLimitUpTomorrowFocus, fetchPositions, limitUpWebSocketUrl, startLimitUpOpenClawReview, updateLimitUpExecution } from "./api";
import { formatMoney, formatPct } from "./format";
import type { LimitUpNextDayPayload, LimitUpNextDayRow, LimitUpOpenClawJob, LimitUpSector, LimitUpStock, LimitUpTomorrowFocusPayload } from "./types";

type Tab = "buy" | "focus" | "watch" | "today" | "sectors";
type SortKey = "default" | "score" | "change" | "open" | "amount" | "streak" | "firstLimit" | "seal" | "limitCount" | "earlyCount";
type LimitRow = LimitUpNextDayRow | LimitUpStock | LimitUpSector;

export function LimitUpPage() {
  const [focusPayload, setFocusPayload] = useState<LimitUpTomorrowFocusPayload | null>(null);
  const [monitorPayload, setMonitorPayload] = useState<LimitUpNextDayPayload | null>(null);
  const [tab, setTab] = useState<Tab>("buy");
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("default");
  const [showOverview, setShowOverview] = useState(true);
  const [showBrief, setShowBrief] = useState(true);
  const [showFullList, setShowFullList] = useState(false);
  const [loading, setLoading] = useState(true);
  const [reviewing, setReviewing] = useState(false);
  const [openClawJob, setOpenClawJob] = useState<LimitUpOpenClawJob | null>(null);
  const [boughtCodes, setBoughtCodes] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState("");
  const [streamState, setStreamState] = useState<"connecting" | "live" | "stale">("connecting");

  async function load(notify = false, silent = false) {
    if (!silent) {
      setLoading(true);
      setStatus("");
    }
    try {
      const [focusResult, monitorResult] = await Promise.allSettled([
        fetchLimitUpTomorrowFocus(false),
        fetchLimitUpNextDayMonitor(notify),
      ]);
      if (focusResult.status === "fulfilled") setFocusPayload(focusResult.value);
      if (monitorResult.status === "fulfilled") setMonitorPayload(monitorResult.value);
      const errors = [focusResult, monitorResult]
        .filter((result) => result.status === "rejected")
        .map((result) => result.reason instanceof Error ? result.reason.message : "接口加载失败");
      const buyCount = monitorResult.status === "fulfilled" ? monitorResult.value.buy_signals.length : 0;
      if (!silent) setStatus(errors.length ? errors.join("；") : notify ? `已推送 ${buyCount} 条买点` : "已刷新");
    } catch (error) {
      if (!silent) setStatus(error instanceof Error ? error.message : "刷新失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }

  async function sendFocusReport() {
    setLoading(true);
    try {
      const next = await fetchLimitUpTomorrowFocus(true);
      setFocusPayload(next);
      setStatus("已推送明日重点");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "推送失败");
    } finally {
      setLoading(false);
    }
  }

  async function runOpenClawReview() {
    setReviewing(true);
    setStatus("规则结果已先出，OpenClaw 将在后台全量复核涨停池...");
    try {
      const result = await startLimitUpOpenClawReview(120, false, 600);
      if (result.payload) setFocusPayload(result.payload);
      setOpenClawJob(result.job);
      setTab("focus");
      setStatus(result.job.summary || "OpenClaw 后台全量复核中，页面会自动刷新");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "OpenClaw 复核失败");
      setReviewing(false);
    }
  }

  async function buyLimitUp(item: LimitUpNextDayRow) {
    if (boughtCodes.has(item.code)) return;
    const defaultPrice = Number(item.official_entry_price || item.official_trigger_price || item.price || 0);
    const priceTextInput = window.prompt(`买入价格：${item.name} ${item.code}`, defaultPrice ? defaultPrice.toFixed(2) : "");
    if (priceTextInput === null) return;
    const price = Number(priceTextInput);
    if (!Number.isFinite(price) || price <= 0) {
      window.alert("买入价格需要大于 0");
      return;
    }
    const sharesText = window.prompt("买入股数", "100");
    if (sharesText === null) return;
    const shares = Math.floor(Number(sharesText));
    if (!Number.isFinite(shares) || shares <= 0) {
      window.alert("买入股数需要大于 0");
      return;
    }
    try {
      await updateLimitUpExecution({
        code: item.code,
        date: monitorPayload?.date || "",
        note: `打板页确认成交：${item.state}${item.buy_unavailable ? "，人工确认买到" : ""}`,
        price,
        shares,
        status: "filled",
      });
      setBoughtCodes((current) => new Set(current).add(item.code));
      setStatus(`已确认成交 ${item.name} ${shares}股，今日 T+1 锁定，明日纳入卖出提醒`);
      void load(false, true);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "买入记录失败");
    }
  }

  async function markExecution(item: LimitUpNextDayRow, nextStatus: "missed" | "abandoned") {
    try {
      await updateLimitUpExecution({
        code: item.code,
        date: monitorPayload?.date || "",
        note: nextStatus === "missed" ? "买不到/未成交" : "盘中放弃",
        price: item.official_entry_price || item.official_trigger_price || item.price || 0,
        shares: 0,
        status: nextStatus,
      });
      setBoughtCodes((current) => {
        const next = new Set(current);
        next.delete(item.code);
        return next;
      });
      setStatus(`${item.name} 已标记${nextStatus === "missed" ? "买不到" : "放弃"}，释放打板信号名额`);
      void load(false, true);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "状态更新失败");
    }
  }

  useEffect(() => {
    if (!openClawJob?.id || !["queued", "running"].includes(openClawJob.status)) return;
    let closed = false;
    const timer = window.setInterval(async () => {
      try {
        const result = await fetchLimitUpOpenClawReviewStatus(openClawJob.id);
        if (closed) return;
        setOpenClawJob(result.job);
        if (result.payload) setFocusPayload(result.payload);
        if (!["queued", "running"].includes(result.job.status)) {
          setReviewing(false);
          window.clearInterval(timer);
          const review = result.payload?.openclaw_review;
          if (result.job.status === "done" && review) {
            setStatus(`OpenClaw 已完成：核心 ${review.core_count} 只，观察 ${review.watch_count} 只，剔除 ${review.avoid_count} 只`);
          } else if (result.job.status === "fallback") {
            setStatus(`${result.job.summary || "OpenClaw 超时"}，已按规则兜底`);
          } else {
            setStatus(result.job.error || result.job.summary || "OpenClaw 后台复核失败");
          }
        } else {
          setStatus(result.job.summary || "OpenClaw 后台复核中");
        }
      } catch (error) {
        if (!closed) setStatus(error instanceof Error ? error.message : "OpenClaw 状态查询失败");
      }
    }, 3000);
    return () => {
      closed = true;
      window.clearInterval(timer);
    };
  }, [openClawJob?.id, openClawJob?.status]);

  useEffect(() => {
    load(false);
    fetchPositions()
      .then((payload) => setBoughtCodes(new Set((payload.positions || []).map((item) => item.code))))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    let closed = false;
    let retryTimer = 0;
    let fallbackTimer = 0;
    let socket: WebSocket | null = null;

    function connect() {
      setStreamState("connecting");
      socket = new WebSocket(limitUpWebSocketUrl());
      socket.onopen = () => {
        setStreamState("live");
        window.clearInterval(fallbackTimer);
      };
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as LimitUpNextDayPayload & { event?: string; tick_driven?: boolean };
          setMonitorPayload(payload);
          setStreamState("live");
          setLoading(false);
        } catch {
          setStreamState("stale");
        }
      };
      socket.onerror = () => setStreamState("stale");
      socket.onclose = () => {
        if (closed) return;
        setStreamState("stale");
        fallbackTimer = window.setInterval(() => load(false, true), 5_000);
        retryTimer = window.setTimeout(connect, 1500);
      };
    }

    connect();
    return () => {
      closed = true;
      window.clearTimeout(retryTimer);
      window.clearInterval(fallbackTimer);
      socket?.close();
    };
  }, []);

  useEffect(() => {
    setSortKey("default");
  }, [tab]);

  const command = buildCommand(focusPayload, monitorPayload);
  const focusDayLabel = focusPayload?.next_date && monitorPayload?.date && focusPayload.next_date === monitorPayload.date ? "今日重点" : "明日重点";
  const leadBuy = monitorPayload?.buy_signals[0];
  const leadFocus = focusPayload?.focus[0];
  const buyBrief = useMemo(() => monitorPayload?.buy_signals || [], [monitorPayload]);
  const coreBrief = useMemo(() => (focusPayload?.focus || []).filter((item) => item.openclaw_tier === "core"), [focusPayload]);
  const activeBrief = useMemo(() => (monitorPayload?.rows || []).filter((item) => item.action === "WATCH"), [monitorPayload]);
  const quality = monitorPayload?.data_quality;
  const permission = monitorPayload?.permission;
  const streamHealth = monitorPayload?.runtime?.limit_up_stream;
  const reliability = monitorPayload?.notification_reliability;

  const sortOptions = getSortOptions(tab);
  const rows = useMemo(() => {
    const text = query.trim().toLowerCase();
    const match = (item: { code?: string; name?: string; sector?: string }) => !text || `${item.code || ""} ${item.name || ""} ${item.sector || ""}`.toLowerCase().includes(text);
    const nextRows: LimitRow[] =
      tab === "buy"
        ? (monitorPayload?.rows || []).filter((item) => item.action !== "PASS").filter(match)
        : tab === "focus"
          ? (focusPayload?.focus || []).filter(match)
          : tab === "watch"
            ? (monitorPayload?.watch_pool || []).filter(match)
            : tab === "today"
              ? (monitorPayload?.today_pool || []).filter(match)
              : (focusPayload?.sectors || []).filter((item) => !text || `${item.sector} ${item.leader?.name || ""}`.toLowerCase().includes(text));
    return sortLimitRows(tab, nextRows, sortKey);
  }, [focusPayload, monitorPayload, query, sortKey, tab]);

  return (
    <main className="limit-page">
      <header className="watch-top">
        <div>
          <h1>隔日涨停监控台</h1>
          <nav>
            <a className="active" href="/limit-up.html">打板</a>
            <a href="/watch.html">关注</a>
            <a href="/review.html">复盘</a>
            <a href="/settings.html">配置</a>
            <a href="/diagnostics.html">诊断</a>
          </nav>
        </div>
        <div className="limit-actions">
          <button onClick={() => load(false)} type="button">
            <RefreshCw size={15} />
            刷新
          </button>
          <button onClick={() => load(true)} type="button">
            <Bell size={15} />
            推送买点
          </button>
          <button onClick={sendFocusReport} type="button">
            <CalendarClock size={15} />
            推送明日重点
          </button>
          <button disabled={reviewing} onClick={runOpenClawReview} type="button">
            <ShieldAlert size={15} />
            {reviewing ? "后台复核中" : "OpenClaw筛选"}
          </button>
        </div>
      </header>

      <CollapseHeader
        count={status || `${streamState === "live" ? "tick实时流" : streamState === "connecting" ? "连接实时流" : "实时流重连中"} · ${focusPayload?.date || "--"} 复盘`}
        onToggle={() => setShowOverview((value) => !value)}
        open={showOverview}
        title="盘面概览"
      />
      {showOverview ? (
        <section className="limit-hero next-day">
          <div className={`limit-command ${command.tone}`}>
            <span>{monitorPayload?.date || focusPayload?.next_date || "--"} · 隔日监控</span>
            <strong>{command.title}</strong>
            <p>{command.detail}</p>
            <div className="limit-command-rules">
              {permission ? <b className={`permission-${permission.status}`}>{permission.label}：{permission.reason}</b> : null}
              {command.rules.map((rule) => <b key={rule}>{rule}</b>)}
            </div>
          </div>

          <LeadBuyCard boughtCodes={boughtCodes} item={leadBuy} fallback={leadFocus} onBuy={buyLimitUp} onExecution={markExecution} status={status} />

          <div className="limit-score-strip">
            <LimitMetric icon={<Target size={16} />} label={focusDayLabel} value={focusPayload?.summary.focus_count ?? "--"} />
            <LimitMetric icon={<ShieldAlert size={16} />} label="核心盯盘" value={focusPayload?.openclaw_review?.core_count ?? "--"} />
            <LimitMetric icon={<Flame size={16} />} label={`昨日涨停池${monitorPayload?.source_date ? ` ${monitorPayload.source_date}` : ""}`} value={monitorPayload?.summary.watch_count ?? "--"} />
            <LimitMetric icon={<TrendingUp size={16} />} label={`今日涨停池${monitorPayload?.date ? ` ${monitorPayload.date}` : ""}`} value={monitorPayload?.summary.today_limit_count ?? monitorPayload?.today_pool?.length ?? "--"} />
            <LimitMetric icon={<CalendarClock size={16} />} label="当前阶段" value={monitorPayload?.phase?.label ?? "--"} />
            <LimitMetric icon={<TrendingUp size={16} />} label="打板信号" value={`${monitorPayload?.summary.buy_signal_count ?? "--"}/3`} />
            <LimitMetric icon={<ShieldAlert size={16} />} label="出手权限" value={permission?.label ?? "--"} />
            <LimitMetric icon={<RefreshCw size={16} />} label="分时就绪" value={quality ? `${quality.kline_ready_count}/${quality.kline_requested_count}` : "--"} />
            <LimitMetric icon={<Bell size={16} />} label="推送成功率" value={reliability ? `${reliability.success_rate}%` : "--"} />
            <LimitMetric icon={<ActivityIcon />} label="流延迟" value={streamHealth?.publish_age_sec != null ? `${streamHealth.publish_age_sec}s` : "--"} />
            <LimitMetric icon={<CalendarClock size={16} />} label="数据更新" value={quality?.updated_at ? ageText(quality.updated_at) : "--"} />
          </div>
          <div className="limit-data-quality">
            <span>{streamState === "live" ? "WebSocket 实时" : "轮询兜底"}</span>
            <span>流状态 {streamHealth?.status || "--"} · 客户端{streamHealth?.client_count ?? 0}</span>
            <span>补发队列 {reliability?.pending_retry_count ?? 0} · 延迟{reliability?.avg_elapsed_ms ?? 0}ms</span>
            <span>行情 {quality?.quote_count ?? 0}/{quality?.watch_count ?? 0}</span>
            <span>涨停池 {quality?.today_pool_ignored ? "已忽略疑似旧数据" : `${quality?.today_pool_count ?? 0}只`}</span>
            <span>分时源 {sourceBreakdown(quality?.kline_source_counts)}</span>
          </div>
        </section>
      ) : null}

      <CollapseHeader
        count={`正式 ${buyBrief.length}/3 · 剩余 ${monitorPayload?.summary.remaining_buy_slots ?? 3} · 机会 ${monitorPayload?.summary.opportunity_count ?? 0} · 异动 ${activeBrief.length}`}
        onToggle={() => setShowBrief((value) => !value)}
        open={showBrief}
        title="盘中盯盘"
      />
      {showBrief ? (
        <section className="limit-brief-board">
          <BriefPanel
            empty="暂无打板信号"
            items={buyBrief}
            meta={`${monitorPayload?.summary.buy_signal_count ?? 0}/3`}
            onOpen={() => {
              setTab("buy");
              setShowFullList(true);
            }}
            renderItem={(item) => <BuyBriefItem item={item as LimitUpNextDayRow} />}
            title="打板信号"
            tone="hot"
          />
          <BriefPanel
            empty="等待 OpenClaw 筛选"
            items={coreBrief}
            meta={`${focusPayload?.openclaw_review?.core_count ?? 0} 只`}
            onOpen={() => {
              setTab("focus");
              setShowFullList(true);
            }}
            renderItem={(item) => <FocusBriefItem item={item as LimitUpStock} />}
            title="核心盯盘"
            tone="core"
          />
          <BriefPanel
            empty="暂无异动未确认"
            items={activeBrief}
            meta={`${monitorPayload?.summary.active_count ?? 0} 只活跃`}
            onOpen={() => {
              setTab("buy");
              setShowFullList(true);
            }}
            renderItem={(item) => <BuyBriefItem item={item as LimitUpNextDayRow} />}
            title="异动未确认"
            tone="watch"
          />
        </section>
      ) : null}

      <CollapseHeader
        count={`${tabLabel(tab)} · ${rows.length} 条`}
        onToggle={() => setShowFullList((value) => !value)}
        open={showFullList}
        title="全量列表"
      />
      {showFullList ? (
        <>
          <section className="limit-toolbar">
            <div className="limit-tabs">
              <button className={tab === "buy" ? "active" : ""} onClick={() => setTab("buy")} type="button">今日买点</button>
              <button className={tab === "focus" ? "active" : ""} onClick={() => setTab("focus")} type="button">明日重点</button>
              <button className={tab === "watch" ? "active" : ""} onClick={() => setTab("watch")} type="button">昨日涨停池</button>
              <button className={tab === "today" ? "active" : ""} onClick={() => setTab("today")} type="button">今日涨停池</button>
              <button className={tab === "sectors" ? "active" : ""} onClick={() => setTab("sectors")} type="button">板块复盘</button>
            </div>
            <label className="limit-search">
              <Search size={15} />
              <input placeholder="搜索代码、名称、板块" value={query} onChange={(event) => setQuery(event.target.value)} />
            </label>
            <label className="limit-sort">
              <ArrowDownWideNarrow size={15} />
              <select value={sortKey} onChange={(event) => setSortKey(event.target.value as SortKey)}>
                {sortOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
          </section>

          <section className="limit-grid">
            {loading && !focusPayload && !monitorPayload ? <div className="empty">加载隔日监控数据中...</div> : null}
            {!loading && !rows.length ? <div className="empty">暂无匹配数据</div> : null}
            {tab === "buy" ? (rows as LimitUpNextDayRow[]).map((item) => <BuyCard bought={boughtCodes.has(item.code)} item={item} key={item.code} onBuy={buyLimitUp} onExecution={markExecution} />) : null}
            {tab === "focus" || tab === "watch" || tab === "today" ? (rows as LimitUpStock[]).map((item) => <FocusCard item={item} key={item.code} />) : null}
            {tab === "sectors" ? (rows as LimitUpSector[]).map((item) => <SectorCard item={item} key={item.sector} />) : null}
          </section>
        </>
      ) : null}
    </main>
  );
}

function LeadBuyCard({
  boughtCodes,
  item,
  fallback,
  onBuy,
  onExecution,
  status,
}: {
  boughtCodes: Set<string>;
  item?: LimitUpNextDayRow;
  fallback?: LimitUpStock;
  onBuy: (item: LimitUpNextDayRow) => void;
  onExecution: (item: LimitUpNextDayRow, status: "missed" | "abandoned") => void;
  status?: string;
}) {
  const bought = item ? boughtCodes.has(item.code) : false;
  const buyStatus = item ? buyRecordStatus(item, bought) : null;
  return (
    <div className="limit-lead-card">
      <header>
        <span>{item ? buySignalLevel(item) : "明日主盯"}</span>
        <b>{item ? `#${item.official_rank || 1}` : fallback?.focus_score ? `${fallback.focus_score.toFixed(0)}分` : "--"}</b>
      </header>
      {item ? (
        <>
          <h2>{item.name} <small>{item.code}</small></h2>
          <p>{item.state} · {item.sector} · 触发{priceText(item.official_trigger_price || item.price)} · 模拟{priceText(item.official_entry_price || item.price)}</p>
          <div className="limit-tags">{item.reasons.slice(0, 4).map((reason) => <i key={reason}>{reason}</i>)}</div>
          <div className="limit-execution-actions">
            <button className="limit-buy-button" disabled={buyStatus?.disabled} onClick={() => onBuy(item)} type="button">{buyStatus?.button || "确认成交"}</button>
            {item.official_buy && item.execution_status !== "filled" ? (
              <>
                <button onClick={() => onExecution(item, "missed")} type="button">买不到</button>
                <button onClick={() => onExecution(item, "abandoned")} type="button">放弃</button>
              </>
            ) : null}
          </div>
        </>
      ) : fallback ? (
        <>
          <h2>{fallback.name} <small>{fallback.code}</small></h2>
          <p>{fallback.next_day_plan || "明日观察"} · {fallback.sector} · {fallback.streak || 1}板 · 首封{fallback.first_limit_time || "--"}</p>
          <div className="limit-tags">{(fallback.focus_reasons || []).slice(0, 4).map((reason) => <i key={reason}>{reason}</i>)}</div>
        </>
      ) : (
        <>
          <h2>等待收盘复盘</h2>
          <p>{status || "收盘后生成明日重点，第二天只监控昨日涨停池。"}</p>
        </>
      )}
    </div>
  );
}

function LimitMetric({ icon, label, value }: { icon: ReactNode; label: string; value: ReactNode }) {
  return (
    <article>
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function ActivityIcon() {
  return <TrendingUp size={16} />;
}

function CollapseHeader({ count, onToggle, open, title }: { count: string; onToggle: () => void; open: boolean; title: string }) {
  return (
    <button className="limit-collapse-header" onClick={onToggle} type="button">
      <span>{title}</span>
      <b>{count}</b>
      {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
    </button>
  );
}

function BriefPanel({
  empty,
  items,
  meta,
  onOpen,
  renderItem,
  title,
  tone,
}: {
  empty: string;
  items: LimitRow[];
  meta: string;
  onOpen: () => void;
  renderItem: (item: LimitRow) => ReactNode;
  title: string;
  tone: "hot" | "core" | "watch";
}) {
  const [expanded, setExpanded] = useState(false);
  const visibleItems = expanded ? items : items.slice(0, 6);
  const hiddenCount = Math.max(0, items.length - visibleItems.length);
  return (
    <section className={`limit-brief-panel ${tone}`}>
      <header>
        <button onClick={onOpen} type="button">{title}</button>
        <span>{meta}</span>
      </header>
      <div className={expanded ? "expanded" : ""}>
        {visibleItems.length ? visibleItems.map((item) => <article key={briefKey(item)}>{renderItem(item)}</article>) : <p className="empty">{empty}</p>}
      </div>
      {items.length > 6 ? (
        <button className="limit-brief-more" onClick={() => setExpanded((value) => !value)} type="button">
          {expanded ? "收起" : `显示更多 ${hiddenCount}`}
        </button>
      ) : null}
    </section>
  );
}

function briefKey(item: LimitRow) {
  if ("code" in item) return item.code;
  return item.sector;
}

function tabLabel(tab: Tab) {
  if (tab === "buy") return "今日买点";
  if (tab === "focus") return "明日重点";
  if (tab === "watch") return "昨日涨停池";
  if (tab === "today") return "今日涨停池";
  return "板块复盘";
}

function BuyBriefItem({ item }: { item: LimitUpNextDayRow }) {
  return (
    <>
      <b>{item.name}<small>{item.code}</small></b>
      <span>{item.official_rank ? `#${item.official_rank} · ` : ""}{item.state} · {item.sector}</span>
      <em>{formatPct(item.change_pct)} / {item.score.toFixed(0)}分</em>
      <i>{item.reasons[0] || item.risk_note}</i>
    </>
  );
}

function FocusBriefItem({ item }: { item: LimitUpStock }) {
  return (
    <>
      <b>{item.name}<small>{item.code}</small></b>
      <span>{item.sector || "未分组"} · {item.streak || 1}板 · 首封{item.first_limit_time || "--"}</span>
      <em>{item.openclaw_score ? `AI ${item.openclaw_score}` : `${item.focus_score?.toFixed(0) || "--"}分`}</em>
      <i>{item.openclaw_summary || item.focus_reasons?.[0] || item.next_day_plan || "观察承接"}</i>
    </>
  );
}

function getSortOptions(tab: Tab): Array<{ value: SortKey; label: string }> {
  if (tab === "buy") {
    return [
      { value: "default", label: "买点优先" },
      { value: "score", label: "分数最高" },
      { value: "change", label: "涨幅最高" },
      { value: "open", label: "高开最高" },
      { value: "amount", label: "成交额最高" },
    ];
  }
  if (tab === "focus") {
    return [
      { value: "default", label: "复盘优先" },
      { value: "score", label: "分数最高" },
      { value: "streak", label: "连板最高" },
      { value: "firstLimit", label: "首封最早" },
      { value: "seal", label: "封单最高" },
    ];
  }
  if (tab === "watch" || tab === "today") {
    return [
      { value: "default", label: tab === "today" ? "今日强度" : "昨日强度" },
      { value: "streak", label: "连板最高" },
      { value: "firstLimit", label: "首封最早" },
      { value: "seal", label: "封单最高" },
      { value: "amount", label: "成交额最高" },
    ];
  }
  return [
    { value: "default", label: "板块强度" },
    { value: "limitCount", label: "涨停最多" },
    { value: "streak", label: "高度最高" },
    { value: "earlyCount", label: "早盘最多" },
    { value: "seal", label: "封单最高" },
  ];
}

function sortLimitRows(tab: Tab, rows: LimitRow[], sortKey: SortKey): LimitRow[] {
  const sorted = [...rows];
  sorted.sort((left, right) => compareLimitRows(tab, left, right, sortKey));
  return sorted;
}

function compareLimitRows(tab: Tab, left: LimitRow, right: LimitRow, sortKey: SortKey): number {
  if (tab === "buy") {
    const a = left as LimitUpNextDayRow;
    const b = right as LimitUpNextDayRow;
    if (sortKey === "score") return desc(a.score, b.score);
    if (sortKey === "change") return desc(a.change_pct, b.change_pct);
    if (sortKey === "open") return desc(a.open_pct, b.open_pct);
    if (sortKey === "amount") return desc(a.amount, b.amount);
    return desc(officialWeight(a), officialWeight(b)) || desc(actionWeight(a.action), actionWeight(b.action)) || desc(a.score, b.score) || desc(a.amount, b.amount);
  }
  if (tab === "sectors") {
    const a = left as LimitUpSector;
    const b = right as LimitUpSector;
    if (sortKey === "limitCount") return desc(a.limit_count, b.limit_count);
    if (sortKey === "streak") return desc(a.max_streak, b.max_streak);
    if (sortKey === "earlyCount") return desc(a.early_count, b.early_count);
    if (sortKey === "seal") return desc(a.seal_amount, b.seal_amount);
    return desc(a.score, b.score) || desc(a.limit_count, b.limit_count) || desc(a.max_streak, b.max_streak);
  }

  const a = left as LimitUpStock;
  const b = right as LimitUpStock;
  if (sortKey === "score") return desc(a.focus_score, b.focus_score);
  if (sortKey === "streak") return desc(a.streak, b.streak);
  if (sortKey === "firstLimit") return asc(timeValue(a.first_limit_time), timeValue(b.first_limit_time));
  if (sortKey === "seal") return desc(a.seal_amount, b.seal_amount);
  if (sortKey === "amount") return desc(a.amount, b.amount);
  if (tab === "focus") {
    return desc(openClawTierWeight(a.openclaw_tier), openClawTierWeight(b.openclaw_tier)) || desc(a.focus_score, b.focus_score) || asc(a.sector_rank ?? 999, b.sector_rank ?? 999) || desc(a.seal_amount, b.seal_amount);
  }
  return desc(a.streak, b.streak) || asc(timeValue(a.first_limit_time), timeValue(b.first_limit_time)) || desc(a.seal_amount, b.seal_amount);
}

function desc(left?: number, right?: number) {
  return (right ?? -Infinity) - (left ?? -Infinity);
}

function asc(left?: number, right?: number) {
  return (left ?? Infinity) - (right ?? Infinity);
}

function actionWeight(action?: string) {
  if (action === "BUY") return 3;
  if (action === "WATCH") return 2;
  return 1;
}

function officialWeight(item: LimitUpNextDayRow) {
  return item.official_buy ? 10 - (item.official_rank || 0) : 0;
}

function openClawTierWeight(value?: string) {
  if (value === "core") return 4;
  if (value === "watch") return 3;
  if (value === "rule") return 2;
  if (value === "unavailable") return 2;
  if (value === "avoid") return 1;
  return 2;
}

function timeValue(value?: string) {
  if (!value) return Infinity;
  const parts = value.split(":").map((part) => Number.parseInt(part, 10));
  if (parts.some((part) => Number.isNaN(part))) return Infinity;
  return (parts[0] || 0) * 3600 + (parts[1] || 0) * 60 + (parts[2] || 0);
}

function BuyCard({
  bought,
  item,
  onBuy,
  onExecution,
}: {
  bought: boolean;
  item: LimitUpNextDayRow;
  onBuy: (item: LimitUpNextDayRow) => void;
  onExecution: (item: LimitUpNextDayRow, status: "missed" | "abandoned") => void;
}) {
  const klineText = klineSignalLabel(item.kline_signal);
  const sourceText = klineSourceLabel(item.kline_source);
  const canBuy = item.action !== "PASS";
  const status = buyRecordStatus(item, bought);
  const showManualActions = item.official_buy && item.execution_status !== "filled" && item.execution_status !== "missed" && item.execution_status !== "abandoned";
  return (
    <article className={`limit-card ${item.action.toLowerCase()}`}>
      <header>
        <b>{item.official_rank ? `${buySignalLevel(item)}#${item.official_rank}` : item.state}</b>
        <span>{status.badge}</span>
      </header>
      <h2>{item.name} <small>{item.code}</small></h2>
      <p>{item.sector} · 昨日{item.source_streak}板 · 高开{formatPct(item.open_pct)} · 涨幅{formatPct(item.change_pct)} · 成交{formatMoney(item.amount)}</p>
      <LimitTimeStrip items={[
        ["触发", item.official_trigger_time || "--"],
        ["模拟成交", item.official_entry_price ? priceText(item.official_entry_price) : "--"],
        ["昨日首封", item.source_first_limit_time || "--"],
        ["今日首封", item.today_first_limit_time || (item.sealed_today ? "--" : "未封板")],
        ["买点状态", item.state],
        ["分时", `${klineText}/${sourceText}`],
        ["最新分钟", minuteTime(item.kline_last_time)],
        ["3分钟", formatPct(item.kline_rise_3m_pct || 0)],
        ["板块", sectorTrendLabel(item.sector_trend)],
        ["五维", dimensionSummary(item)],
      ]} />
      <div className="limit-tags">{item.reasons.map((reason) => <i key={reason}>{reason}</i>)}</div>
      <footer>
        <span className="limit-risk-text"><ShieldAlert size={14} />{item.risk_note}</span>
        {canBuy ? (
          <div className="limit-execution-actions">
            <button className="limit-buy-button" disabled={status.disabled} onClick={() => onBuy(item)} type="button">{status.button}</button>
            {showManualActions ? (
              <>
                <button onClick={() => onExecution(item, "missed")} type="button">买不到</button>
                <button onClick={() => onExecution(item, "abandoned")} type="button">放弃</button>
              </>
            ) : null}
          </div>
        ) : null}
      </footer>
    </article>
  );
}

function buyRecordStatus(item: LimitUpNextDayRow, bought: boolean) {
  if (item.execution_status === "filled") return { badge: "已成交", button: "已成交", disabled: true };
  if (item.execution_status === "missed") return { badge: "买不到", button: "买不到", disabled: true };
  if (item.execution_status === "abandoned") return { badge: "已放弃", button: "已放弃", disabled: true };
  if (bought) return { badge: "已记录", button: "已记录买入", disabled: true };
  if (item.official_buy) return { badge: `${buySignalLevel(item)}#${item.official_rank || ""}`, button: isSealSignal(item) ? "确认成交" : "试探成交", disabled: false };
  if (item.buy_unavailable) return { badge: "买不到", button: "人工确认成交", disabled: false };
  return { badge: `${item.score.toFixed(0)}分`, button: "记录买入", disabled: false };
}

function isSealSignal(item: LimitUpNextDayRow) {
  return Boolean(item.sealed_today || item.state === "首封确认" || item.state === "回封确认");
}

function buySignalLevel(item: LimitUpNextDayRow) {
  return isSealSignal(item) ? "正式买点" : "试探买点";
}

function sectorTrendLabel(value?: string) {
  if (value === "enhancing") return "增强";
  if (value === "fading") return "退潮";
  if (value === "normal") return "正常";
  return "--";
}

function dimensionSummary(item: LimitUpNextDayRow) {
  const dimensions = item.kline_dimensions || {};
  return `拉${dimensions.pull || 0}/承${dimensions.reclaim || 0}/封${dimensions.seal || 0}/量${dimensions.volume || 0}`;
}

function ageText(ts: number) {
  const age = Math.max(0, Math.round(Date.now() / 1000 - Number(ts || 0)));
  if (age < 60) return `${age}s前`;
  return `${Math.floor(age / 60)}m前`;
}

function sourceBreakdown(counts?: Record<string, number>) {
  if (!counts || !Object.keys(counts).length) return "未拉取";
  return Object.entries(counts).map(([key, value]) => `${klineSourceLabel(key)}${value}`).join("/");
}

function klineSignalLabel(value?: string) {
  if (value === "strong") return "走势确认";
  if (value === "watch") return "走势观察";
  if (value === "weak") return "走势转弱";
  return "等待分时";
}

function klineSourceLabel(value?: string) {
  if (value === "sina") return "新浪";
  if (value === "cache") return "缓存";
  if (value === "eastmoney-trends") return "东财";
  if (value === "eastmoney-kline") return "东财K";
  if (value === "tdx") return "TDX";
  return "无源";
}

function minuteTime(value?: string) {
  if (!value) return "--";
  const text = String(value);
  return text.includes(" ") ? text.split(" ").pop() || text : text;
}

function priceText(value?: number | string) {
  const number = Number(value || 0);
  return number > 0 ? number.toFixed(2) : "--";
}

function FocusCard({ item }: { item: LimitUpStock }) {
  const tier = openClawTierLabel(item.openclaw_tier);
  return (
    <article className={`limit-card ${item.openclaw_tier === "avoid" ? "risk" : item.openclaw_tier === "core" ? "buy" : ""}`}>
      <header>
        <b>{tier || item.next_day_plan || item.sector || "观察"}</b>
        <span>{item.openclaw_score ? `AI ${item.openclaw_score}` : item.focus_score ? `${item.focus_score.toFixed(0)}分` : `${item.streak || 1}板`}</span>
      </header>
      <h2>{item.name} <small>{item.code}</small></h2>
      <p>{item.sector || "未分组"} · {item.streak || 1}板 · 首封 {item.first_limit_time || "--"} · 封单 {formatMoney(item.seal_amount || 0)}</p>
      <LimitTimeStrip items={[
        ["首封", item.first_limit_time || "--"],
        ["末封", item.last_limit_time || "--"],
        ["炸板", `${item.open_board_count || 0}次`],
      ]} />
      {item.openclaw_summary ? <p className="limit-openclaw-summary">{item.openclaw_summary}</p> : null}
      <div className="limit-tags">{(item.focus_reasons || []).slice(0, 4).map((reason) => <i key={reason}>{reason}</i>)}</div>
      {item.openclaw_risks?.length ? <footer><ShieldAlert size={14} />{item.openclaw_risks[0]}</footer> : null}
    </article>
  );
}

function LimitTimeStrip({ items }: { items: Array<[string, string]> }) {
  return (
    <div className="limit-time-strip">
      {items.map(([label, value]) => (
        <span key={label}>
          <small>{label}</small>
          <b>{value}</b>
        </span>
      ))}
    </div>
  );
}

function openClawTierLabel(value?: string) {
  if (value === "core") return "核心盯盘";
  if (value === "watch") return "Agent观察";
  if (value === "avoid") return "风险剔除";
  if (value === "rule") return "规则候选";
  if (value === "unavailable") return "复核不可用";
  return "";
}

function SectorCard({ item }: { item: LimitUpSector }) {
  return (
    <article className="limit-card sector">
      <header>
        <b>{item.sector}</b>
        <span>{item.score.toFixed(0)}分</span>
      </header>
      <h2>{item.limit_count}只涨停 <small>高度{item.max_streak}板</small></h2>
      <p>早盘封板 {item.early_count} · 龙头 {item.leader?.name || "--"} · 封单 {formatMoney(item.seal_amount)}</p>
      <div className="limit-stock-strip">
        {item.stocks.slice(0, 8).map((stock) => <span key={stock.code}>{stock.name}</span>)}
      </div>
    </article>
  );
}

function buildCommand(focus?: LimitUpTomorrowFocusPayload | null, monitor?: LimitUpNextDayPayload | null): { title: string; detail: string; tone: "go" | "wait" | "risk"; rules: string[] } {
  const buy = monitor?.buy_signals?.[0];
  if (buy) {
    return {
      title: `买点触发：${buy.name}`,
      detail: `${buy.state}，来自昨日涨停池。满足后再推送，不再按固定时间刷屏。`,
      tone: "go",
      rules: ["只做强承接", "封板确认优先", "跌破开盘价放弃"],
    };
  }
  if (monitor && monitor.summary.active_count > 0) {
    return {
      title: "有异动，等买点确认",
      detail: `昨日涨停池 ${monitor.summary.watch_count} 只，当前活跃 ${monitor.summary.active_count} 只，但还未达到买入推送条件。`,
      tone: "wait",
      rules: ["不抢弱反弹", "等放量走强", "只盯昨日涨停"],
    };
  }
  if (focus?.focus?.length) {
    const label = focus.next_date && monitor?.date && focus.next_date === monitor.date ? "今日主盯" : "明日主盯";
    const detailLabel = label === "今日主盯" ? "今日重点" : "明日重点";
    return {
      title: `${label}：${focus.focus[0].name}`,
      detail: `收盘复盘已生成 ${focus.summary.focus_count} 只${detailLabel}。第二天实时监控这些和昨日涨停全池。`,
      tone: "wait",
      rules: ["收盘定池", "次日监控", "信号触发才推"],
    };
  }
  return {
    title: "等待收盘复盘",
    detail: "收盘后先生成明日重点，第二天只监控昨天涨停板股票。",
    tone: "risk",
    rules: ["先有复盘池", "再盯次日承接", "无买点不推送"],
  };
}
