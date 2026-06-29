import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Activity, CheckCircle2, RefreshCw, ShieldAlert, Target, TrendingUp } from "lucide-react";
import { fetchLimitUpSystemReview } from "./api";
import { formatPct, trendClass } from "./format";
import type { LimitUpSystemReviewPayload, LimitUpSystemReviewRecord, LimitUpSystemReviewRow, LimitUpSystemTrade } from "./types";

const SYSTEM_START_CAPITAL = 100000;
const SYSTEM_MAX_POSITIONS = 3;

export function ReviewPage() {
  const [ledger, setLedger] = useState<LimitUpSystemReviewPayload | null>(null);
  const [selectedDate, setSelectedDate] = useState("");
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState("");

  async function loadLedger(date = selectedDate) {
    setLoading(true);
    setStatus("");
    try {
      const payload = await fetchLimitUpSystemReview(date);
      setLedger(payload);
      if (!date && payload.date) setSelectedDate(payload.date);
      setStatus("已刷新");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "复盘加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadLedger(selectedDate);
  }, [selectedDate]);

  const selected = ledger?.selected || null;
  const positions = ledger?.positions || selected?.ending_positions || [];
  const trades = ledger?.trades || selected?.trades || [];
  const rules = ledger?.rules || [];
  const headline = useMemo(() => buildHeadline(selected), [selected]);

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
          <button onClick={() => loadLedger()} type="button">
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
        <ReviewMetric title="交易日" value={ledger?.stats.trade_days ?? 0} icon={<Activity size={16} />} />
      </section>

      <section className="leader-review-brief">
        <article className={selected?.decision?.level || "warn"}>
          <small>{selected?.source_date || "--"} 涨停池 → {selected?.date || "--"} 账户复盘</small>
          <strong>{headline}</strong>
          <span>{selected?.decision?.action || "等待账本"} · {selected?.decision?.reason || "暂无系统打板样本"}</span>
        </article>
        <div>
          {rules.map((rule) => (
            <span className={rule.level} key={rule.title}>
              <b>{rule.title}</b>
              {rule.badge}
            </span>
          ))}
        </div>
      </section>

      <section className="review-grid">
        <Panel title="账户状态" meta={loading ? "同步中" : selected?.date || "--"}>
          <div className="review-record-list limit-up-daily-pnl-list">
            <article className={selectedTone(selected)}>
              <div className="review-stock">
                <strong>{selected?.date || "--"}</strong>
                <span>系统持仓 {selected?.position_count ?? 0}/{SYSTEM_MAX_POSITIONS}</span>
                <em>买入 {selected?.buy_count ?? 0} · 卖出 {selected?.sell_count ?? 0}</em>
              </div>
              <MetricCell label="期初净值" value={formatMoney(selected?.start_equity ?? SYSTEM_START_CAPITAL, false)} />
              <MetricCell label="现金" value={formatMoney(selected?.cash ?? SYSTEM_START_CAPITAL, false)} />
              <MetricCell label="市值" value={formatMoney(selected?.market_value ?? 0, false)} />
              <MetricCell label="当日盈亏" value={formatMoney(selected?.pnl_amount ?? 0)} tone={trendClass(selected?.pnl_amount ?? 0)} />
              <MetricCell label="当日收益" value={formatPct(selected?.pnl_pct ?? 0)} tone={trendClass(selected?.pnl_pct ?? 0)} />
              <p>{accountSummary(selected)}</p>
            </article>
          </div>
        </Panel>

        <Panel title="当前持仓" meta={`${positions.length}/${SYSTEM_MAX_POSITIONS}`}>
          <div className="executable-review-list limit-up-system-review-list">
            {positions.map((item) => <PositionCard item={item} key={item.code} />)}
            {!positions.length ? <EmptyReview text={status || "暂无系统持仓"} /> : null}
          </div>
        </Panel>
      </section>

      <section className="review-grid">
        <Panel title="当日流水" meta={`${trades.length} 笔`}>
          <div className="review-record-list limit-up-review-table">
            {trades.map((trade, index) => <TradeRow index={index} trade={trade} key={`${trade.side}-${trade.code}-${index}`} />)}
            {!trades.length ? <EmptyReview text="当日无买卖流水" /> : null}
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
        <Panel title="资金曲线" meta={`${ledger?.stats.trade_days ?? 0} 日`}>
          <div className="equity-curve">
            {(ledger?.history || []).map((item) => (
              <article className={item.date === selected?.date ? "active" : ""} key={item.date} onClick={() => setSelectedDate(item.date)}>
                <span>{item.date.slice(5)}</span>
                <b className={trendClass(item.pnl_amount)}>{formatMoney(item.pnl_amount)}</b>
                <i style={{ height: `${curveHeight(item, ledger?.history || [])}%` }} />
                <small>{formatMoney(item.equity || SYSTEM_START_CAPITAL, false)}</small>
              </article>
            ))}
            {!ledger?.history.length ? <EmptyReview text="暂无资金曲线" /> : null}
          </div>
        </Panel>
      </section>

      <section className="review-record-panel">
        <header>
          <h2>账户明细</h2>
          <span>只统计系统打板记录，初始资金 {formatMoney(ledger?.capital ?? SYSTEM_START_CAPITAL, false)}</span>
        </header>
        <div className="review-record-list limit-up-review-table">
          {(selected?.rows || []).map((item, index) => (
            <article className={rowTone(item)} key={`${item.trade_action}-${item.code}-${index}`}>
              <div className="review-stock">
                <strong>{item.name}</strong>
                <span>{item.code} · {item.sector} · {actionLabel(item.trade_action)}</span>
                <em>{item.position_status} · {item.action}</em>
              </div>
              <MetricCell label="成本" value={formatPrice(item.entry_price)} />
              <MetricCell label="现价" value={formatPrice(item.price)} />
              <MetricCell label="股数" value={`${item.shares}`} />
              <MetricCell label="盈亏" value={formatMoney(item.pnl_amount)} tone={trendClass(item.pnl_amount)} />
              <MetricCell label="收益" value={formatPct(item.pnl_pct)} tone={trendClass(item.pnl_pct)} />
              <MetricCell label="费用" value={formatMoney(item.fee || 0, false)} />
              <MetricCell label="计划" value={item.planned_action || "--"} />
              <MetricCell label="实际" value={item.actual_action || item.action || "--"} />
              <p>{item.t1_status ? `${item.t1_status} · ` : ""}{item.failure_reason || item.state || "按纪律执行"}</p>
            </article>
          ))}
          {!selected?.rows.length ? <EmptyReview text="暂无账户明细" /> : null}
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

function PositionCard({ item }: { item: LimitUpSystemReviewRow }) {
  return (
    <article className={rowTone(item)}>
      <header>
        <div>
          <strong>{item.name}</strong>
          <span>{item.code} · {item.sector} · {item.opened_at || item.trade_date || "--"}</span>
        </div>
        <b>{formatPct(item.pnl_pct)}</b>
      </header>
      <div className="executable-review-meta system-review-meta">
        <MetricCell label="成本" value={formatPrice(item.entry_price)} />
        <MetricCell label="现价" value={formatPrice(item.price)} />
        <MetricCell label="股数" value={`${item.shares}`} />
        <MetricCell label="市值" value={formatMoney(item.market_value || 0, false)} />
        <MetricCell label="盈亏" value={formatMoney(item.pnl_amount)} tone={trendClass(item.pnl_amount)} />
        <MetricCell label="处理" value={item.actual_action || item.action || "--"} />
      </div>
      <p>{item.t1_status ? `${item.t1_status} · ` : ""}{item.state || "继续观察"}</p>
    </article>
  );
}

function TradeRow({ trade, index }: { trade: LimitUpSystemTrade; index: number }) {
  return (
    <article className={trade.side === "buy" ? "good" : "warn"}>
      <div className="review-stock">
        <strong>{trade.name}</strong>
        <span>{trade.code} · {trade.side === "buy" ? "买入" : "卖出"} · #{index + 1}</span>
        <em>{trade.reason}</em>
      </div>
      <MetricCell label="价格" value={formatPrice(trade.price)} />
      <MetricCell label="股数" value={`${trade.shares}`} />
      <MetricCell label="金额" value={formatMoney(trade.amount, false)} />
      <MetricCell label="费用" value={formatMoney(trade.fee, false)} />
      <p>{trade.execution_status ? `${executionLabel(trade.execution_status)} · ` : ""}{trade.reason}</p>
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

function buildHeadline(record: LimitUpSystemReviewRecord | null) {
  if (!record) return "等待系统打板账本";
  return `${formatMoney(record.pnl_amount)} · ${formatPct(record.pnl_pct)} · 净值 ${formatMoney(record.equity || SYSTEM_START_CAPITAL, false)}`;
}

function selectedTone(record: LimitUpSystemReviewRecord | null) {
  if (!record) return "warn";
  if ((record.pnl_amount || 0) > 0) return "good";
  if ((record.pnl_amount || 0) < 0) return "danger";
  return "warn";
}

function rowTone(item: LimitUpSystemReviewRow) {
  if (item.trade_action === "sell" || item.position_status === "已剔除") return "danger";
  if ((item.pnl_pct || 0) > 0 || item.position_status === "持有中") return "good";
  return "warn";
}

function actionLabel(action?: string) {
  if (action === "buy") return "新开仓";
  if (action === "sell") return "已卖出";
  if (action === "hold") return "持仓";
  return "记录";
}

function executionLabel(value?: string) {
  if (value === "filled") return "实盘成交";
  if (value === "missed") return "买不到";
  if (value === "abandoned") return "已放弃";
  if (value === "simulated") return "系统模拟";
  if (value === "held") return "持仓处理";
  return value || "--";
}

function accountSummary(record: LimitUpSystemReviewRecord | null) {
  if (!record) return "暂无系统打板记录。";
  return `期末净值 ${formatMoney(record.equity || SYSTEM_START_CAPITAL, false)}，现金 ${formatMoney(record.cash, false)}，市值 ${formatMoney(record.market_value || 0, false)}，回撤 ${formatPct(record.drawdown_pct || 0)}。`;
}

function formatMoney(value: number, signed = true) {
  const amount = Number(value || 0);
  const sign = signed && amount > 0 ? "+" : "";
  return `${sign}${amount.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

function formatPrice(value?: number) {
  const price = Number(value || 0);
  return price ? price.toFixed(2) : "--";
}

function curveHeight(item: LimitUpSystemReviewRecord, history: LimitUpSystemReviewRecord[]) {
  const equities = history.map((record) => Number(record.equity || SYSTEM_START_CAPITAL));
  const min = Math.min(...equities, SYSTEM_START_CAPITAL);
  const max = Math.max(...equities, SYSTEM_START_CAPITAL);
  if (max <= min) return 48;
  return 18 + ((Number(item.equity || SYSTEM_START_CAPITAL) - min) / (max - min)) * 72;
}
