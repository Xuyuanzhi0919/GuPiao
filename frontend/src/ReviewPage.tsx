import { useEffect, useMemo, useState, type ReactNode } from "react";
import { CheckCircle2, Flame, RefreshCw, ShieldAlert, Target, TrendingUp } from "lucide-react";
import { fetchLimitUpNextDayMonitor, fetchLimitUpSystemReview } from "./api";
import { formatPct, trendClass } from "./format";
import type { LimitUpNextDayPayload, LimitUpNextDayRow, LimitUpSystemReviewPayload, LimitUpSystemReviewRecord, LimitUpSystemReviewRow } from "./types";

type ReviewMode = "portfolio" | "missed" | "watch" | "all";
type ReviewLevel = "good" | "warn" | "danger";
const SIM_PORTFOLIO_START_DATE = "2026-06-17";
const SYSTEM_START_CAPITAL = 100000;
const SYSTEM_MAX_POSITIONS = 3;

export function ReviewPage() {
  const [monitor, setMonitor] = useState<LimitUpNextDayPayload | null>(null);
  const [ledger, setLedger] = useState<LimitUpSystemReviewPayload | null>(null);
  const [selectedDate, setSelectedDate] = useState("");
  const [mode, setMode] = useState<ReviewMode>("portfolio");
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState("");

  async function loadAll() {
    setLoading(true);
    setStatus("");
    try {
      const [monitorData, ledgerData] = await Promise.all([
        fetchLimitUpNextDayMonitor(false),
        fetchLimitUpSystemReview(selectedDate),
      ]);
      setMonitor(monitorData);
      setLedger(ledgerData);
      if (!selectedDate && ledgerData.date) setSelectedDate(ledgerData.date);
      setStatus("已刷新");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "复盘加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
  }, [selectedDate]);

  const review = useMemo(() => buildLimitUpReview(monitor), [monitor]);
  const systemReview = useMemo(() => buildSystemLedgerView(ledger), [ledger]);
  const rows = useMemo(() => {
    if (mode === "portfolio") return review.portfolioRows;
    if (mode === "missed") return review.missedBuyRows;
    if (mode === "watch") return review.rows.filter((item) => item.row.action === "WATCH");
    return review.rows.filter((item) => item.row.action !== "PASS");
  }, [mode, review.missedBuyRows, review.portfolioRows, review.rows]);

  return (
    <main className="review-page">
      <header className="watch-top">
        <div>
          <h1>打板复盘</h1>
          <nav>
            <a href="/limit-up.html">打板</a>
            <a href="/watch.html">关注</a>
            <a className="active" href="/review.html">复盘</a>
            <a href="/settings.html">配置</a>
            <a href="/diagnostics.html">诊断</a>
          </nav>
        </div>
        <div className="review-top-actions">
          <select value={selectedDate} onChange={(event) => setSelectedDate(event.target.value)}>
            {(ledger?.dates || []).map((date) => <option key={date} value={date}>{date}</option>)}
          </select>
          <button onClick={loadAll} type="button">
            <RefreshCw size={15} />
            刷新
          </button>
        </div>
      </header>

      <section className="review-metrics">
        <ReviewMetric title="当前净值" value={formatMoney(ledger?.stats.equity ?? SYSTEM_START_CAPITAL, false)} icon={<Target size={16} />} />
        <ReviewMetric title="累计收益" value={formatPct(ledger?.stats.total_return_pct ?? 0)} icon={<TrendingUp size={16} />} />
        <ReviewMetric title="最大回撤" value={formatPct(ledger?.stats.max_drawdown_pct ?? 0)} icon={<ShieldAlert size={16} />} />
        <ReviewMetric title="胜率" value={formatPct(ledger?.stats.win_rate ?? 0)} icon={<CheckCircle2 size={16} />} />
        <ReviewMetric title="交易日" value={ledger?.stats.trade_days ?? 0} icon={<Flame size={16} />} />
      </section>

      <section className="leader-review-brief">
        <article>
          <small>{systemReview.sourceDate || "--"} 涨停池 → {systemReview.date || "--"} 收盘复核</small>
          <strong>{systemReview.headline}</strong>
          <span>封板 {systemReview.sealedCount}/{systemReview.rows.length || SYSTEM_MAX_POSITIONS} · 调仓 {systemReview.rebalanceCount} · 剔除 {systemReview.clearCount}</span>
        </article>
        <div>
          {review.rules.map((rule) => (
            <span className={rule.level} key={rule.title}>
              <b>{rule.title}</b>
              {rule.badge}
            </span>
          ))}
        </div>
      </section>

      <section className="review-grid">
        <Panel title="系统打板" meta={loading ? "同步中" : `${systemReview.rows.length} / ${SYSTEM_MAX_POSITIONS}`}>
          <div className="executable-review-list limit-up-system-review-list">
            {systemReview.rows.map((item) => <SystemOfficialReviewCard item={item} key={item.code} />)}
            {!systemReview.rows.length ? <EmptyReview text={status || "暂无系统正式打板记录"} /> : null}
          </div>
        </Panel>

        <Panel title="机会复核" meta={loading ? "同步中" : `${review.portfolioRows.length} / 3`}>
          <div className="executable-review-list limit-up-review-list">
            {review.portfolioRows.map((item) => <LimitUpReviewCard item={item} key={item.row.code} />)}
            {!review.portfolioRows.length ? <EmptyReview text="暂无模拟持仓样本" /> : null}
          </div>
        </Panel>
      </section>

      <section className="review-grid wide">
        <Panel title="资金表现" meta={monitor?.date || "--"}>
          <div className="review-record-list limit-up-daily-pnl-list">
            <article className={systemReview.pnlAmount < 0 ? "danger" : systemReview.pnlAmount > 0 ? "good" : "warn"}>
                <div className="review-stock">
                  <strong>{monitor?.date || "--"}</strong>
                  <span>系统正式打板 {systemReview.rows.length} 只</span>
                  <em>继续 {systemReview.holdCount} · 调仓 {systemReview.rebalanceCount}</em>
                </div>
                <MetricCell label="计划本金" value={formatMoney(SYSTEM_START_CAPITAL, false)} />
                <MetricCell label="当日盈亏" value={formatMoney(systemReview.pnlAmount)} tone={trendClass(systemReview.pnlAmount)} />
                <MetricCell label="收益率" value={formatPct(systemReview.pnlPct)} tone={trendClass(systemReview.pnlPct)} />
                <MetricCell label="最好" value={formatPct(systemReview.bestPnlPct)} tone={trendClass(systemReview.bestPnlPct)} />
                <MetricCell label="最差" value={formatPct(systemReview.worstPnlPct)} tone={trendClass(systemReview.worstPnlPct)} />
                <p>{systemReview.dailyText}</p>
              </article>
          </div>
        </Panel>
      </section>

      <section className="review-grid">
        <Panel title="资金曲线" meta={`${ledger?.stats.trade_days ?? 0} 日`}>
          <div className="equity-curve">
            {(ledger?.history || []).map((item) => (
              <article key={item.date}>
                <span>{item.date.slice(5)}</span>
                <b className={trendClass(item.pnl_amount)}>{formatMoney(item.pnl_amount)}</b>
                <i style={{ height: `${curveHeight(item, ledger?.history || [])}%` }} />
                <small>{formatMoney(item.equity || SYSTEM_START_CAPITAL, false)}</small>
              </article>
            ))}
            {!ledger?.history.length ? <EmptyReview text="暂无资金曲线" /> : null}
          </div>
        </Panel>

        <Panel title="失败归因" meta={`${ledger?.failure_attribution.length ?? 0} 类`}>
          <div className="failure-chip-list">
            {(ledger?.failure_attribution || []).map((item) => (
              <article key={item.reason}>
                <strong>{item.reason}</strong>
                <span>{item.count}</span>
              </article>
            ))}
            {!ledger?.failure_attribution.length ? <EmptyReview text="暂无失败归因" /> : null}
          </div>
        </Panel>
      </section>

      <section className="review-grid wide">
        <Panel title="交易日归档" meta={`${ledger?.history.length ?? 0} 条`}>
          <div className="review-record-list limit-up-archive-list">
            {(ledger?.history || []).slice().reverse().map((item) => (
              <article className={item.pnl_amount < 0 ? "danger" : item.pnl_amount > 0 ? "good" : "warn"} key={item.date} onClick={() => setSelectedDate(item.date)}>
                <div className="review-stock">
                  <strong>{item.date}</strong>
                  <span>系统打板 {item.position_count} · 封板 {item.seal_count}</span>
                  <em>状态 {item.hold_count}/{item.rebalance_count}/{item.clear_count}</em>
                </div>
                <MetricCell label="盈亏" value={formatMoney(item.pnl_amount)} tone={trendClass(item.pnl_amount)} />
                <MetricCell label="收益率" value={formatPct(item.pnl_pct)} tone={trendClass(item.pnl_pct)} />
                <MetricCell label="净值" value={formatMoney(item.equity || SYSTEM_START_CAPITAL, false)} />
                <MetricCell label="回撤" value={formatPct(item.drawdown_pct || 0)} tone={trendClass(item.drawdown_pct || 0)} />
                <p>{archiveSummary(item)}</p>
              </article>
            ))}
          </div>
        </Panel>
      </section>

      <section className="review-record-panel">
        <header>
          <h2>明细</h2>
          <div className="segmented">
            <button className={mode === "portfolio" ? "active" : ""} onClick={() => setMode("portfolio")} type="button">持仓3只</button>
            <button className={mode === "missed" ? "active" : ""} onClick={() => setMode("missed")} type="button">未入选</button>
            <button className={mode === "watch" ? "active" : ""} onClick={() => setMode("watch")} type="button">观察</button>
            <button className={mode === "all" ? "active" : ""} onClick={() => setMode("all")} type="button">全部</button>
          </div>
        </header>
        <div className="review-record-list limit-up-review-table">
          {rows.map((item) => (
            <article className={item.level} key={item.row.code}>
              <div className="review-stock">
                <strong>{item.row.name}</strong>
                <span>{item.row.code} · {item.row.sector} · {item.row.state}</span>
                <em>{item.rank ? `模拟持仓#${item.rank}` : item.verdict}</em>
              </div>
              <MetricCell label="复盘涨幅" value={formatPct(item.row.change_pct)} tone={trendClass(item.row.change_pct)} />
              <MetricCell label="开盘后" value={formatPct(item.fromOpen)} tone={trendClass(item.fromOpen)} />
              <MetricCell label="最高" value={item.hasRange ? formatPct(item.highFromOpen) : "--"} tone={item.hasRange ? trendClass(item.highFromOpen) : "flat"} />
              <MetricCell label="最低" value={item.hasRange ? formatPct(item.lowFromOpen) : "--"} tone={item.hasRange ? trendClass(item.lowFromOpen) : "flat"} />
              <MetricCell label="分时" value={klineLabel(item.row.kline_signal)} />
              <MetricCell label="分数" value={`${Number(item.row.score || 0).toFixed(0)}分`} />
              <p>{item.verdict}</p>
            </article>
          ))}
          {!rows.length ? <EmptyReview text={status || "暂无复盘样本"} /> : null}
        </div>
      </section>
    </main>
  );
}

function Panel({ title, meta, children }: { title: string; meta: string; children: ReactNode }) {
  return (
    <section className="review-panel">
      <header>
        <h2>{title}</h2>
        <span>{meta}</span>
      </header>
      {children}
    </section>
  );
}

function ReviewMetric({ title, value, icon }: { title: string; value: string | number; icon: ReactNode }) {
  return (
    <article>
      <span>{icon}</span>
      <small>{title}</small>
      <strong>{value}</strong>
    </article>
  );
}

function LimitUpReviewCard({ item }: { item: LimitUpReviewRow }) {
  return (
    <article className={item.level}>
      <header>
        <div>
          <strong>{item.row.name}</strong>
          <span>{item.row.code} · {item.row.sector} · {item.row.state}</span>
        </div>
        <b>{item.rank ? `持仓#${item.rank}` : item.verdict}</b>
      </header>
      <div className="executable-review-meta">
        <MetricCell label="复盘涨幅" value={formatPct(item.row.change_pct)} tone={trendClass(item.row.change_pct)} />
        <MetricCell label="开盘后" value={formatPct(item.fromOpen)} tone={trendClass(item.fromOpen)} />
        <MetricCell label="最高回报" value={formatPct(item.highFromOpen)} tone={trendClass(item.highFromOpen)} />
      </div>
      <p>{item.verdict}</p>
    </article>
  );
}

function SystemOfficialReviewCard({ item }: { item: LimitUpSystemReviewRow }) {
  const level = item.position_status === "持有中" ? "good" : item.position_status === "已剔除" ? "danger" : "warn";
  return (
    <article className={level}>
      <header>
        <div>
          <strong>{item.name}</strong>
          <span>{item.code} · {item.sector} · 正式#{item.rank}</span>
        </div>
        <b>{item.action}</b>
      </header>
      <div className="executable-review-meta system-review-meta">
        <MetricCell label="成交" value={item.entry_price ? item.entry_price.toFixed(2) : "--"} />
        <MetricCell label="现价" value={item.price ? item.price.toFixed(2) : "--"} />
        <MetricCell label="资金" value={formatMoney(item.allocated_capital, false)} />
        <MetricCell label="股数" value={`${item.shares}`} />
        <MetricCell label="盈亏" value={formatMoney(item.pnl_amount)} tone={trendClass(item.pnl_amount)} />
        <MetricCell label="收益" value={formatPct(item.pnl_pct)} tone={trendClass(item.pnl_pct)} />
      </div>
      <p>{item.position_status}{item.failure_reason ? ` · ${item.failure_reason}` : ""}</p>
    </article>
  );
}

function MetricCell({ label, value, tone = "flat" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="review-cell">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </div>
  );
}

function EmptyReview({ text }: { text: string }) {
  return <div className="empty">{text}</div>;
}

type LimitUpReviewRow = {
  row: LimitUpNextDayRow;
  selected: boolean;
  rank: number;
  fromOpen: number;
  highFromOpen: number;
  lowFromOpen: number;
  hasRange: boolean;
  verdict: string;
  level: ReviewLevel;
};

function buildLimitUpReview(payload: LimitUpNextDayPayload | null) {
  const rawRows = payload?.rows || [];
  const portfolioEnabled = Boolean(payload?.date && String(payload.date) >= SIM_PORTFOLIO_START_DATE);
  const activeRows = rawRows.filter((item) => item.action !== "PASS");
  const rows = activeRows
    .map(buildLimitUpReviewRow)
    .sort((left, right) => actionWeight(right.row.action) - actionWeight(left.row.action) || right.row.score - left.row.score);
  const buyRows = rows.filter((item) => item.row.action === "BUY").map((item, index) => ({ ...item, rank: index + 1, selected: index < 3 }));
  const selectedCodes = new Set(buyRows.filter((item) => item.selected).map((item) => item.row.code));
  const mergedRows = rows.map((item) => {
    const buyItem = buyRows.find((row) => row.row.code === item.row.code);
    return buyItem || { ...item, selected: selectedCodes.has(item.row.code), rank: 0 };
  });
  const portfolioRows = portfolioEnabled ? buyRows.slice(0, 3) : [];
  const missedBuyRows = portfolioEnabled ? buyRows.slice(3) : buyRows;
  const sealedRows = portfolioRows.filter((item) => item.row.sealed_today);
  const weakRows = portfolioRows.filter((item) => item.level === "danger");
  const avgClosePct = average(portfolioRows.map((item) => item.row.change_pct));
  const avgFromOpenPct = average(portfolioRows.map((item) => item.fromOpen));
  const sealRate = portfolioRows.length ? (sealedRows.length / portfolioRows.length) * 100 : 0;
  const summary = !portfolioEnabled && buyRows.length
    ? `模拟持仓从 ${SIM_PORTFOLIO_START_DATE} 开始记录。今日只展示机会池 ${buyRows.length} 条，不倒推买入。`
    : portfolioRows.length
      ? `今日只模拟买入前 ${portfolioRows.length} 只，封板收盘 ${sealedRows.length} 只，平均复盘涨幅 ${formatPct(avgClosePct)}。机会池共 ${buyRows.length} 条。`
    : "今日没有触发买点，等待下一次次日监控样本。";
  const action = portfolioEnabled ? reviewAction(sealRate, avgFromOpenPct, weakRows.length, portfolioRows.length) : "今天未盘中执行，不计入模拟持仓回测；从明天开始按前3只记录真实口径。";
  return {
    rows: mergedRows,
    portfolioRows,
    missedBuyRows,
    buyCount: buyRows.length,
    weakCount: weakRows.length,
    avgClosePct,
    avgFromOpenPct,
    sealRate,
    summary,
    action,
    rules: portfolioEnabled
      ? buildReviewRules(sealRate, avgFromOpenPct, weakRows.length, portfolioRows.length)
      : buildPendingRules(buyRows.length),
  };
}

function buildSystemLedgerView(payload: LimitUpSystemReviewPayload | null) {
  const selected = payload?.selected || null;
  const rows = selected?.rows || [];
  const pnlAmount = selected?.pnl_amount || 0;
  const pnlPct = selected?.pnl_pct || 0;
  const headline = selected ? `${formatMoney(pnlAmount)} · ${formatPct(pnlPct)}` : "等待系统打板";
  const dailyText = rows.length
    ? rows.map((item) => `${item.name} ${formatMoney(item.pnl_amount)} ${item.position_status}`).join(" / ")
    : "暂无系统正式打板样本";
  return {
    date: selected?.date || payload?.date || "",
    sourceDate: selected?.source_date || "",
    rows,
    pnlAmount,
    pnlPct,
    bestPnlPct: selected?.best_pnl_pct || 0,
    worstPnlPct: selected?.worst_pnl_pct || 0,
    sealedCount: selected?.seal_count || 0,
    holdCount: selected?.hold_count || 0,
    rebalanceCount: selected?.rebalance_count || 0,
    clearCount: selected?.clear_count || 0,
    sealRate: selected?.seal_rate || 0,
    headline,
    dailyText,
  };
}

function buildLimitUpReviewRow(row: LimitUpNextDayRow): LimitUpReviewRow {
  const fromOpen = Number(row.close_from_open_pct ?? percentMove(row.price, row.open));
  const hasRange = row.high_from_open_pct !== undefined || row.low_from_open_pct !== undefined || (row.high !== undefined && row.low !== undefined);
  const highFromOpen = Number(row.high_from_open_pct ?? (row.high ? percentMove(row.high, row.open) : 0));
  const lowFromOpen = Number(row.low_from_open_pct ?? (row.low ? percentMove(row.low, row.open) : 0));
  const sealed = Boolean(row.sealed_today);
  const strongKline = row.kline_signal === "strong";
  const failed = row.action === "BUY" && !sealed && (fromOpen < -2 || row.change_pct < 3 || row.kline_signal === "weak");
  const good = sealed || (fromOpen >= 2 && strongKline);
  const level: ReviewLevel = failed ? "danger" : good ? "good" : "warn";
  const verdict = sealed ? "封板收盘" : failed ? "买点走弱" : good ? "确认有效" : "一般兑现";
  return { row, selected: false, rank: 0, fromOpen, highFromOpen, lowFromOpen, hasRange, verdict, level };
}

function buildPendingRules(buyCount: number) {
  return [
    {
      title: "起算日",
      badge: "明天",
      level: "warn" as ReviewLevel,
      text: `起算 ${SIM_PORTFOLIO_START_DATE}`,
      detail: "避免把没有真实盘中执行的信号倒推成持仓收益。",
    },
    {
      title: "机会池",
      badge: `${buyCount}`,
      level: buyCount ? "good" as ReviewLevel : "warn" as ReviewLevel,
      text: buyCount ? "有机会" : "无买点",
      detail: "机会池不参与模拟收益统计。",
    },
    {
      title: "复盘涨幅",
      badge: "记录",
      level: "good" as ReviewLevel,
      text: "记录收益",
      detail: "用于比较入选时强度、收盘表现和开盘后收益。",
    },
  ];
}

function buildReviewRules(sealRate: number, avgFromOpenPct: number, weakCount: number, buyCount: number) {
  const weakRate = buyCount ? (weakCount / buyCount) * 100 : 0;
  return [
    {
      title: "买点质量",
      badge: sealRate >= 70 ? "强" : sealRate >= 45 ? "中" : "弱",
      level: sealRate >= 70 ? "good" as ReviewLevel : sealRate >= 45 ? "warn" as ReviewLevel : "danger" as ReviewLevel,
      text: sealRate >= 70 ? "封板率达标，明日继续优先做走势确认票。" : "封板率不足，明日只做回封和强分时确认。",
      detail: `今日封板收盘率 ${sealRate.toFixed(1)}%。`,
    },
    {
      title: "开盘承接",
      badge: avgFromOpenPct >= 1 ? "进攻" : avgFromOpenPct >= 0 ? "观察" : "收缩",
      level: avgFromOpenPct >= 1 ? "good" as ReviewLevel : avgFromOpenPct >= 0 ? "warn" as ReviewLevel : "danger" as ReviewLevel,
      text: avgFromOpenPct >= 1 ? "高开后仍能向上，开盘承接有效。" : "高开后兑现一般，明日降低追高权重。",
      detail: `买点平均开盘后收益 ${formatPct(avgFromOpenPct)}。`,
    },
    {
      title: "失败过滤",
      badge: `${weakCount}`,
      level: weakRate >= 30 ? "danger" as ReviewLevel : weakRate > 0 ? "warn" as ReviewLevel : "good" as ReviewLevel,
      text: weakCount ? "走弱样本需要复核分时和板块强度，不扩大同类仓位。" : "暂无明显走弱买点。",
      detail: `走弱占比 ${weakRate.toFixed(1)}%。`,
    },
  ];
}

function reviewAction(sealRate: number, avgFromOpenPct: number, weakCount: number, buyCount: number) {
  if (!buyCount) return "没有买点时不强行复盘盈亏，重点看收盘后明日重点池。";
  if (sealRate >= 70 && avgFromOpenPct >= 1) return "今日打板链路有效，明日继续围绕昨日涨停池，只做分时确认和封板确认。";
  if (weakCount / Math.max(1, buyCount) >= 0.3) return "走弱样本偏多，明日收紧买点，只推封板确认或强回封。";
  return "买点有效性中等，明日保留监控，但仓位按半档执行。";
}

function klineLabel(value?: string) {
  if (value === "strong") return "走势确认";
  if (value === "watch") return "走势观察";
  if (value === "weak") return "走势转弱";
  return "等待分时";
}

function actionWeight(action?: string) {
  if (action === "BUY") return 3;
  if (action === "WATCH") return 2;
  return 1;
}

function average(values: number[]) {
  const valid = values.filter((item) => Number.isFinite(item));
  return valid.length ? valid.reduce((sum, item) => sum + item, 0) / valid.length : 0;
}

function percentMove(price?: number, base?: number) {
  return price && base ? (price / base - 1) * 100 : 0;
}

function formatMoney(value: number, signed = true) {
  const amount = Number(value || 0);
  const sign = signed && amount > 0 ? "+" : "";
  return `${sign}${amount.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

function curveHeight(item: LimitUpSystemReviewRecord, history: LimitUpSystemReviewRecord[]) {
  const equities = history.map((record) => Number(record.equity || SYSTEM_START_CAPITAL));
  const min = Math.min(...equities, SYSTEM_START_CAPITAL);
  const max = Math.max(...equities, SYSTEM_START_CAPITAL);
  if (max <= min) return 48;
  return 18 + ((Number(item.equity || SYSTEM_START_CAPITAL) - min) / (max - min)) * 72;
}

function archiveSummary(item: LimitUpSystemReviewRecord) {
  if (!item.rows.length) return "无系统打板";
  return item.rows.map((row) => `${row.name} ${formatMoney(row.pnl_amount)} ${row.position_status}`).join(" / ");
}
