import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, Bell, EyeOff, Heart, Plus, RefreshCw, Search, Star, Target, Trash2, TrendingUp } from "lucide-react";
import { addPreference, addTradeRecord, fetchAiTradeReview, fetchCandidates, fetchKline, fetchMarketQuotes, fetchNotifications, fetchOpenClawReview, fetchPositions, fetchPreferences, fetchRadarDetail, fetchSnapshot, fetchTradeMarks, lookupStocks, removePosition, removePreference, removeTradeMark, searchStocks, setTradeMark, upsertPosition } from "./api";
import { formatAge, formatMoney, formatPct, trendClass } from "./format";
import type { AiTradeReviewPayload, Candidate, CandidatePayload, KlineBar, KlinePayload, MarketQuote, NotificationItem, NotificationPayload, OpenClawReviewPayload, Position, PositionsPayload, PreferencePayload, RadarDetailPayload, Signal, SnapshotPayload, StockOption, Track, TradeMark, TradeMarksPayload } from "./types";

type ManagedCode = {
  code: string;
  name: string;
  sector: string;
  list: "watchlist" | "blocklist";
  candidate?: Candidate;
  signal?: Signal;
  track?: Track;
  notification?: NotificationItem;
  kline?: KlinePayload;
  tradeMark?: TradeMark;
  position?: Position;
  quote?: MarketQuote;
};

const WATCHLIST_KEY = "radarWatchlist";
const BLOCKLIST_KEY = "radarBlocklist";
const TRADE_MARKS_KEY = "radarTradeMarks";
const AUTO_POSITION_REVIEW_KEY = "radarAutoPositionReview";
type WatchMode = "watchlist" | "positions" | "bought" | "blocklist" | "all";

export function WatchPage() {
  const [snapshot, setSnapshot] = useState<SnapshotPayload>({});
  const [candidatePayload, setCandidatePayload] = useState<CandidatePayload>({});
  const [preferences, setPreferences] = useState<PreferencePayload["preferences"]>({ watchlist: [], blocklist: [] });
  const [tradeMarks, setTradeMarks] = useState<TradeMarksPayload["marks"]>({});
  const [positions, setPositions] = useState<PositionsPayload["positions"]>([]);
  const [notifications, setNotifications] = useState<NotificationPayload | null>(null);
  const [klineByCode, setKlineByCode] = useState<Record<string, KlinePayload>>({});
  const [quoteByCode, setQuoteByCode] = useState<Record<string, MarketQuote>>({});
  const [stockMetaByCode, setStockMetaByCode] = useState<Record<string, StockOption>>({});
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<WatchMode>("watchlist");
  const [stateFilter, setStateFilter] = useState<"all" | "active" | "alerted" | "idle">("all");
  const [selectedWatchStock, setSelectedWatchStock] = useState<StockOption | null>(null);
  const [watchStockOptions, setWatchStockOptions] = useState<StockOption[]>([]);
  const [watchStockOptionsOpen, setWatchStockOptionsOpen] = useState(false);
  const [watchAddStatus, setWatchAddStatus] = useState("");
  const [positionDraft, setPositionDraft] = useState({ code: "", price: "", shares: "" });
  const [selectedPositionStock, setSelectedPositionStock] = useState<StockOption | null>(null);
  const [stockOptions, setStockOptions] = useState<StockOption[]>([]);
  const [stockOptionsOpen, setStockOptionsOpen] = useState(false);
  const [detailItem, setDetailItem] = useState<ManagedCode | null>(null);
  const [detailPayload, setDetailPayload] = useState<RadarDetailPayload | null>(null);
  const [detailKline, setDetailKline] = useState<KlinePayload | null>(null);
  const [detailReview, setDetailReview] = useState<AiTradeReviewPayload | null>(null);
  const [detailOpenClaw, setDetailOpenClaw] = useState<OpenClawReviewPayload | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [openClawLoading, setOpenClawLoading] = useState(false);
  const [positionOpenClawByCode, setPositionOpenClawByCode] = useState<Record<string, OpenClawReviewPayload>>({});
  const [positionOpenClawLoading, setPositionOpenClawLoading] = useState<Record<string, boolean>>({});
  const [allPositionsOpenClawLoading, setAllPositionsOpenClawLoading] = useState(false);
  const [autoPositionReview, setAutoPositionReview] = useState(() => localStorage.getItem(AUTO_POSITION_REVIEW_KEY) === "true");
  const [autoPositionReviewStatus, setAutoPositionReviewStatus] = useState("");
  const autoReviewLastRunRef = useRef(0);
  const [loading, setLoading] = useState(true);

  async function loadAll() {
    setLoading(true);
    try {
      const [snapshotData, candidateData, preferenceData, notificationData, marksData, positionData] = await Promise.all([
        fetchSnapshot(),
        fetchCandidates(),
        fetchPreferences(),
        fetchNotifications(80),
        fetchTradeMarks(),
        fetchPositions(),
      ]);
      setSnapshot(snapshotData);
      setCandidatePayload(candidateData);
      setPreferences(preferenceData.preferences);
      setNotifications(notificationData);
      setTradeMarks(marksData.marks || {});
      setPositions(positionData.positions || []);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
    const timer = window.setInterval(loadAll, 10000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    localStorage.setItem(AUTO_POSITION_REVIEW_KEY, String(autoPositionReview));
  }, [autoPositionReview]);

  const rows = useMemo(() => {
    return buildManagedRows(preferences, candidatePayload.candidates || [], snapshot.signals || [], snapshot.tracked_alerts || [], notifications?.notifications || [], klineByCode, tradeMarks, stockMetaByCode, positions, quoteByCode)
      .filter((item) => matchWatchMode(item, mode))
      .filter((item) => stateFilter === "all" || rowState(item) === stateFilter)
      .filter((item) => `${item.code} ${item.name} ${item.sector}`.toLowerCase().includes(query.trim().toLowerCase()))
      .sort((left, right) => rowPriority(right) - rowPriority(left));
  }, [candidatePayload, klineByCode, mode, notifications, positions, preferences, query, quoteByCode, snapshot, stateFilter, stockMetaByCode, tradeMarks]);

  useEffect(() => {
    const tradeMarkCodes = Object.keys(tradeMarks).filter(Boolean);
    const positionCodes = positions.map((item) => item.code).filter(Boolean);
    const codes = [...new Set([...preferences.watchlist, ...preferences.blocklist, ...tradeMarkCodes, ...positionCodes])].filter(Boolean);
    const missing = codes.filter((code) => !klineByCode[code]);
    if (!missing.length) return;
    let cancelled = false;
    Promise.allSettled(missing.map((code) => fetchKline(code, 120).then((payload) => ({ code, payload })))).then((results) => {
      if (cancelled) return;
      setKlineByCode((current) => {
        const next = { ...current };
        for (const result of results) {
          if (result.status === "fulfilled") {
            next[result.value.payload.code] = result.value.payload;
          } else {
            const reason = result.reason instanceof Error ? result.reason.message : String(result.reason || "请求失败");
            const code = missing[results.indexOf(result)];
            next[code] = { code, symbol: code, date: "", source: "eastmoney", bars: [], error: reason };
          }
        }
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [klineByCode, positions, preferences.blocklist, preferences.watchlist, tradeMarks]);

  useEffect(() => {
    const codes = [
      ...preferences.watchlist,
      ...preferences.blocklist,
      ...Object.keys(tradeMarks),
      ...positions.map((item) => item.code),
    ].filter(Boolean);
    const missing = [...new Set(codes)].filter((code) => !stockMetaByCode[code]);
    if (!missing.length) return;
    let cancelled = false;
    lookupStocks(missing).then((payload) => {
      if (cancelled) return;
      setStockMetaByCode((current) => ({ ...current, ...(payload.stocks || {}) }));
    }).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [positions, preferences.blocklist, preferences.watchlist, stockMetaByCode, tradeMarks]);

  useEffect(() => {
    const codes = positions.map((item) => item.code).filter(Boolean);
    if (!codes.length) return;
    let cancelled = false;
    async function refreshQuotes() {
      try {
        const payload = await fetchMarketQuotes(codes);
        if (!cancelled) setQuoteByCode((current) => ({ ...current, ...(payload.quotes || {}) }));
      } catch {
        // Keep the last successful quote on screen; kline/candidate prices remain as fallback.
      }
    }
    refreshQuotes();
    const timer = window.setInterval(refreshQuotes, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [positions]);

  const watchRows = rows.filter((item) => item.list === "watchlist");
  const activeRows = watchRows.filter((item) => item.signal || item.candidate);
  const alertedRows = watchRows.filter((item) => item.notification);
  const boughtCount = new Set([...Object.entries(tradeMarks).filter(([, item]) => item.mark === "bought").map(([code]) => code), ...positions.map((item) => item.code)]).size;
  const positionRows = useMemo(() => buildPositionRows(positions, rows, candidatePayload.candidates || [], snapshot.signals || [], klineByCode, stockMetaByCode, quoteByCode), [candidatePayload.candidates, klineByCode, positions, quoteByCode, rows, snapshot.signals, stockMetaByCode]);
  const positionSummary = useMemo(() => summarizePositions(positionRows), [positionRows]);

  useEffect(() => {
    if (!stockOptionsOpen) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const payload = await searchStocks(positionDraft.code, 12);
        if (!cancelled) setStockOptions(payload.stocks || []);
      } catch {
        if (!cancelled) setStockOptions([]);
      }
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [positionDraft.code, stockOptionsOpen]);

  useEffect(() => {
    if (!watchStockOptionsOpen) return;
    if (!query.trim()) {
      setWatchStockOptions([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const payload = await searchStocks(query, 12);
        if (!cancelled) setWatchStockOptions(payload.stocks || []);
      } catch {
        if (!cancelled) setWatchStockOptions([]);
      }
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query, watchStockOptionsOpen]);

  useEffect(() => {
    if (!detailItem) return;
    let cancelled = false;
    setDetailLoading(true);
    setDetailPayload(null);
    setDetailKline(null);
    setDetailReview(null);
    setDetailOpenClaw(null);
    Promise.allSettled([
      fetchRadarDetail(detailItem.code),
      fetchKline(detailItem.code, 120),
      fetchAiTradeReview(detailItem.code),
    ]).then(([detailResult, klineResult, reviewResult]) => {
      if (cancelled) return;
      setDetailPayload(detailResult.status === "fulfilled" ? detailResult.value : null);
      setDetailKline(klineResult.status === "fulfilled" ? klineResult.value : detailItem.kline || null);
      setDetailReview(reviewResult.status === "fulfilled" ? reviewResult.value : null);
      setDetailLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [detailItem]);

  useEffect(() => {
    if (!autoPositionReview) {
      setAutoPositionReviewStatus("");
      return;
    }
    const tick = () => {
      const state = openingReviewWindowState(new Date());
      setAutoPositionReviewStatus(state.label);
      if (!state.inWindow || !positions.length || allPositionsOpenClawLoading) return;
      const now = Date.now();
      if (now - autoReviewLastRunRef.current < 180_000) return;
      autoReviewLastRunRef.current = now;
      void runAllPositionsOpenClawReview("auto");
    };
    tick();
    const timer = window.setInterval(tick, 30_000);
    return () => window.clearInterval(timer);
  }, [allPositionsOpenClawLoading, autoPositionReview, positions]);

  async function move(code: string, list: "watchlist" | "blocklist") {
    const payload = await addPreference(list, code);
    setPreferences(payload.preferences);
    syncLocalPreferences(payload.preferences);
  }

  async function remove(code: string, list: "watchlist" | "blocklist", hasTradeMark = false) {
    const [payload, marksPayload] = await Promise.all([
      removePreference(list, code),
      hasTradeMark ? removeTradeMark(code) : Promise.resolve(null),
    ]);
    setPreferences(payload.preferences);
    syncLocalPreferences(payload.preferences);
    if (marksPayload) {
      setTradeMarks(marksPayload.marks || {});
      syncLocalTradeMarks(marksPayload.marks || {});
    }
  }

  async function addManualWatch() {
    const fallback = watchStockOptions[0];
    const code = selectedWatchStock?.code || normalizeStockCode(query) || fallback?.code;
    if (!code) {
      setWatchAddStatus("请输入 6 位股票代码，或从候选里选择股票");
      return;
    }
    const meta = selectedWatchStock || fallback || stockMetaByCode[code] || (await lookupStocks([code]).then((payload) => payload.stocks?.[code]).catch(() => undefined));
    const payload = await addPreference("watchlist", code);
    setPreferences(payload.preferences);
    syncLocalPreferences(payload.preferences);
    if (meta) {
      setStockMetaByCode((current) => ({ ...current, [code]: meta }));
    }
    setSelectedWatchStock(null);
    setWatchStockOptionsOpen(false);
    setMode("watchlist");
    setStateFilter("all");
    setQuery("");
    setWatchAddStatus(`${meta?.name || code} 已加入关注`);
    window.setTimeout(() => setWatchAddStatus(""), 2200);
  }

  function selectWatchOption(option: StockOption) {
    setSelectedWatchStock(option);
    setQuery(option.code);
    setWatchStockOptionsOpen(false);
    setWatchAddStatus("");
  }

  async function clearTradeMark(code: string) {
    const payload = await removeTradeMark(code);
    setTradeMarks(payload.marks || {});
    syncLocalTradeMarks(payload.marks || {});
  }

  async function markTrade(code: string, mark: TradeMark) {
    const payload = await setTradeMark(code, mark);
    setTradeMarks(payload.marks || {});
    syncLocalTradeMarks(payload.marks || {});
    setDetailItem((current) => current && current.code === code ? { ...current, tradeMark: mark } : current);
  }

  async function runOpenClawReview(code: string) {
    setOpenClawLoading(true);
    try {
      const payload = await fetchOpenClawReview(code);
      setDetailOpenClaw(payload);
    } finally {
      setOpenClawLoading(false);
    }
  }

  async function runPositionOpenClawReview(code: string) {
    setPositionOpenClawLoading((current) => ({ ...current, [code]: true }));
    try {
      const payload = await fetchOpenClawReview(code);
      setPositionOpenClawByCode((current) => ({ ...current, [code]: payload }));
    } finally {
      setPositionOpenClawLoading((current) => ({ ...current, [code]: false }));
    }
  }

  async function runAllPositionsOpenClawReview(source: "manual" | "auto" = "manual") {
    const codes = positions.map((item) => item.code).filter(Boolean);
    if (!codes.length) return;
    setAllPositionsOpenClawLoading(true);
    setAutoPositionReviewStatus(source === "auto" ? "开盘自动分析中" : "持仓分析中");
    try {
      for (const code of codes) {
        setPositionOpenClawLoading((current) => ({ ...current, [code]: true }));
        try {
          const payload = await fetchOpenClawReview(code);
          setPositionOpenClawByCode((current) => ({ ...current, [code]: payload }));
        } finally {
          setPositionOpenClawLoading((current) => ({ ...current, [code]: false }));
        }
      }
    } finally {
      setAllPositionsOpenClawLoading(false);
      setAutoPositionReviewStatus(`${new Date().toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" })} 已完成持仓分析`);
    }
  }

  async function addPosition() {
    const code = selectedPositionStock?.code || normalizeStockCode(positionDraft.code);
    const price = Number(positionDraft.price);
    const shares = Number(positionDraft.shares);
    if (!code || !price || !shares) return;
    const meta = selectedPositionStock || stockMetaByCode[code];
    const row = rows.find((item) => item.code === code) || buildManagedRows(preferences, candidatePayload.candidates || [], snapshot.signals || [], snapshot.tracked_alerts || [], notifications?.notifications || [], klineByCode, tradeMarks, stockMetaByCode, positions, quoteByCode).find((item) => item.code === code);
    const payload = await upsertPosition({
      code,
      name: meta?.name || row?.name,
      sector: meta?.sector || row?.sector,
      price,
      shares,
    });
    void addTradeRecord({ code, name: meta?.name || row?.name || code, sector: meta?.sector || row?.sector || "", side: "buy", price, shares, reason: "手动加入持仓", source: "watch-page" });
    setPositions(payload.positions || []);
    setPositionDraft({ code: "", price: "", shares: "" });
    setSelectedPositionStock(null);
    const preferencePayload = await addPreference("watchlist", code);
    setPreferences(preferencePayload.preferences);
    syncLocalPreferences(preferencePayload.preferences);
  }

  function selectPositionOption(option: StockOption) {
    setSelectedPositionStock(option);
    setPositionDraft((current) => ({
      ...current,
      code: option.code,
    }));
    setStockOptionsOpen(false);
  }

  async function deletePosition(code: string, trade?: { name?: string; sector?: string; price?: number; shares?: number; reason?: string }) {
    const [positionPayload, markPayload, preferencePayload] = await Promise.all([
      removePosition(code),
      removeTradeMark(code),
      removePreference("watchlist", code),
    ]);
    setPositions(positionPayload.positions || []);
    setTradeMarks(markPayload.marks || {});
    syncLocalTradeMarks(markPayload.marks || {});
    setPreferences(preferencePayload.preferences);
    syncLocalPreferences(preferencePayload.preferences);
    setQuoteByCode((current) => {
      const next = { ...current };
      delete next[code];
      return next;
    });
    if (trade?.price && trade.shares) {
      void addTradeRecord({ code, name: trade.name || code, sector: trade.sector || "", side: "sell", price: trade.price, shares: trade.shares, reason: trade.reason || "清仓", source: "watch-page" });
    }
  }

  async function increasePosition(item: PositionRow) {
    const priceText = window.prompt(`加仓价格：${item.name}`, item.currentPrice ? item.currentPrice.toFixed(2) : item.buyPrice.toFixed(2));
    if (priceText === null) return;
    const price = Number(priceText);
    if (!Number.isFinite(price) || price <= 0) {
      window.alert("加仓价格需要大于 0");
      return;
    }
    const sharesText = window.prompt("加仓股数", "100");
    if (sharesText === null) return;
    const shares = Math.floor(Number(sharesText));
    if (!Number.isFinite(shares) || shares <= 0) {
      window.alert("加仓股数需要大于 0");
      return;
    }
    const nextShares = item.shares + shares;
    const nextPrice = ((item.buyPrice * item.shares) + (price * shares)) / nextShares;
    const payload = await upsertPosition({ code: item.code, name: item.name, sector: item.sector, price: nextPrice, shares: nextShares });
    void addTradeRecord({ code: item.code, name: item.name, sector: item.sector, side: "add", price, shares, reason: "手动加仓", source: "watch-page" });
    setPositions(payload.positions || []);
  }

  async function decreasePosition(item: PositionRow) {
    const priceText = window.prompt(`减仓价格：${item.name}`, item.currentPrice ? item.currentPrice.toFixed(2) : item.buyPrice.toFixed(2));
    if (priceText === null) return;
    const price = Number(priceText);
    if (!Number.isFinite(price) || price <= 0) {
      window.alert("减仓价格需要大于 0");
      return;
    }
    const sharesText = window.prompt("减仓股数", String(Math.min(100, item.shares)));
    if (sharesText === null) return;
    const shares = Math.floor(Number(sharesText));
    if (!Number.isFinite(shares) || shares <= 0) {
      window.alert("减仓股数需要大于 0");
      return;
    }
    const amount = price * shares;
    if (!window.confirm(`确认减仓 ${item.name}：${shares} 股，价格 ${price.toFixed(2)}，约 ${formatSignedMoney(amount).replace("+", "")}`)) return;
    if (shares >= item.shares) {
      await deletePosition(item.code, { name: item.name, sector: item.sector, price, shares: item.shares, reason: "减仓数量达到持仓，按清仓处理" });
      return;
    }
    const payload = await upsertPosition({ code: item.code, name: item.name, sector: item.sector, price: item.buyPrice, shares: item.shares - shares });
    void addTradeRecord({ code: item.code, name: item.name, sector: item.sector, side: "reduce", price, shares, reason: "手动减仓", source: "watch-page" });
    setPositions(payload.positions || []);
  }

  async function clearPosition(item: PositionRow) {
    const priceText = window.prompt(`清仓价格：${item.name}`, item.currentPrice ? item.currentPrice.toFixed(2) : item.buyPrice.toFixed(2));
    if (priceText === null) return;
    const price = Number(priceText);
    if (!Number.isFinite(price) || price <= 0) {
      window.alert("清仓价格需要大于 0");
      return;
    }
    if (!window.confirm(`确认清仓 ${item.name}：${item.shares} 股，价格 ${price.toFixed(2)}`)) return;
    await deletePosition(item.code, { name: item.name, sector: item.sector, price, shares: item.shares, reason: "手动清仓" });
  }

  return (
    <main className="watch-page">
      <header className="watch-top">
        <div>
          <h1>关注管理</h1>
          <nav>
            <a href="/limit-up.html">打板</a>
            <a className="active" href="/watch.html">关注</a>
            <a href="/review.html">复盘</a>
            <a href="/settings.html">配置</a>
            <a href="/diagnostics.html">诊断</a>
          </nav>
        </div>
        <button onClick={loadAll} type="button">
          <RefreshCw size={15} />
          刷新
        </button>
      </header>

      <section className="watch-metrics">
        <Metric title="关注" value={preferences.watchlist.length} icon={<Heart size={16} />} />
        <Metric title="活跃关注" value={activeRows.length} icon={<TrendingUp size={16} />} />
        <Metric title="已买" value={boughtCount} icon={<Target size={16} />} />
        <Metric
          variant="asset"
          title="持仓总览"
          value={formatMoney(positionSummary.marketValue)}
          detail={`盈亏 ${formatSignedMoney(positionSummary.profit)}`}
          extra={`成本 ${formatMoney(positionSummary.cost)}`}
          detailTone={trendClass(positionSummary.profit)}
          icon={<TrendingUp size={16} />}
        />
        <Metric title="最近提醒" value={alertedRows.length} icon={<Bell size={16} />} />
        <Metric title="屏蔽" value={preferences.blocklist.length} icon={<EyeOff size={16} />} />
      </section>

      <section className="position-panel">
        <header>
          <h2>
            <Target size={16} />
            当前持仓
          </h2>
          <div className="position-header-actions">
            <span>市值 {formatMoney(positionSummary.marketValue)} · 成本 {formatMoney(positionSummary.cost)}</span>
            <button className={autoPositionReview ? "active" : ""} disabled={!positions.length} onClick={() => setAutoPositionReview((value) => !value)} type="button">
              {autoPositionReview ? "开盘自动开" : "开盘自动关"}
            </button>
            <button disabled={!positions.length || allPositionsOpenClawLoading} onClick={() => runAllPositionsOpenClawReview("manual")} type="button">
              <Target size={14} />
              {allPositionsOpenClawLoading ? "分析中" : "一键分析持仓"}
            </button>
            {autoPositionReviewStatus ? <small>{autoPositionReviewStatus}</small> : null}
          </div>
        </header>
        <div className="position-form">
          <div className="position-code-field">
            <label>
              <span>股票</span>
              <input
                value={selectedPositionStock ? `${selectedPositionStock.name} / ${selectedPositionStock.code}` : positionDraft.code}
                onBlur={() => window.setTimeout(() => setStockOptionsOpen(false), 140)}
                onChange={(event) => {
                  setPositionDraft((current) => ({ ...current, code: event.target.value }));
                  setSelectedPositionStock(null);
                  setStockOptionsOpen(true);
                }}
                onFocus={() => setStockOptionsOpen(true)}
                placeholder="代码 / 名称 / 板块"
              />
            </label>
            {stockOptionsOpen && stockOptions.length ? (
              <div className="position-options">
                {stockOptions.map((option) => (
                  <button key={option.symbol || option.code} onMouseDown={(event) => event.preventDefault()} onClick={() => selectPositionOption(option)} type="button">
                    <strong>{option.name}</strong>
                    <span>{option.code} · {option.sector || "--"} · {option.symbol}</span>
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          <label>
            <span>买入价</span>
            <input value={positionDraft.price} onChange={(event) => setPositionDraft((current) => ({ ...current, price: event.target.value }))} placeholder="0.00" type="number" />
          </label>
          <label>
            <span>股数</span>
            <input value={positionDraft.shares} onChange={(event) => setPositionDraft((current) => ({ ...current, shares: event.target.value }))} placeholder="100" type="number" />
          </label>
          <button onClick={addPosition} type="button">
            <Plus size={14} />
            加入持仓
          </button>
        </div>
        {positionRows.length ? (
          <div className="position-list">
            {positionRows.map((item) => (
              <article key={item.code}>
                <div>
                  <strong>{item.name}</strong>
                  <span>{item.code} · {item.sector}</span>
                </div>
                <PositionCell label="持仓" value={`${item.shares}股`} />
                <PositionCell label="成本" value={item.buyPrice.toFixed(2)} />
                <PositionCell label="现价" value={item.currentPrice ? item.currentPrice.toFixed(2) : "--"} />
                <PositionCell label="盈亏" value={formatSignedMoney(item.profit)} tone={trendClass(item.profit)} />
                <PositionCell label="收益率" value={formatPct(item.returnPct)} tone={trendClass(item.returnPct)} />
                <div className="position-actions">
                  <button disabled={Boolean(positionOpenClawLoading[item.code])} onClick={() => runPositionOpenClawReview(item.code)} type="button">
                    <Target size={14} />
                    {positionOpenClawLoading[item.code] ? "复盘中" : "OpenClaw复盘"}
                  </button>
                  <button onClick={() => increasePosition(item)} type="button">
                    <Plus size={14} />
                    加仓
                  </button>
                  <button onClick={() => decreasePosition(item)} type="button">
                    减仓
                  </button>
                  <button onClick={() => clearPosition(item)} type="button">
                    <Trash2 size={14} />
                    清仓
                  </button>
                </div>
                {positionOpenClawByCode[item.code] ? <PositionOpenClawReview review={positionOpenClawByCode[item.code]} /> : null}
              </article>
            ))}
          </div>
        ) : (
          <div className="empty">暂无持仓</div>
        )}
      </section>

      <section className="watch-toolbar">
        <div className="segmented">
          <button className={mode === "watchlist" ? "active" : ""} onClick={() => setMode("watchlist")} type="button">关注</button>
          <button className={mode === "positions" ? "active" : ""} onClick={() => setMode("positions")} type="button">持仓</button>
          <button className={mode === "bought" ? "active" : ""} onClick={() => setMode("bought")} type="button">已买</button>
          <button className={mode === "blocklist" ? "active" : ""} onClick={() => setMode("blocklist")} type="button">屏蔽</button>
          <button className={mode === "all" ? "active" : ""} onClick={() => setMode("all")} type="button">全部</button>
        </div>
        <div className="segmented state-segmented">
          <button className={stateFilter === "all" ? "active" : ""} onClick={() => setStateFilter("all")} type="button">状态</button>
          <button className={stateFilter === "active" ? "active" : ""} onClick={() => setStateFilter("active")} type="button">活跃</button>
          <button className={stateFilter === "alerted" ? "active" : ""} onClick={() => setStateFilter("alerted")} type="button">提醒</button>
          <button className={stateFilter === "idle" ? "active" : ""} onClick={() => setStateFilter("idle")} type="button">等待</button>
        </div>
        <div className="watch-search-field">
          <label>
            <Search size={15} />
            <input
              value={query}
              onBlur={() => window.setTimeout(() => setWatchStockOptionsOpen(false), 140)}
              onChange={(event) => {
                setQuery(event.target.value);
                setSelectedWatchStock(null);
                setWatchAddStatus("");
                setWatchStockOptionsOpen(true);
              }}
              onFocus={() => setWatchStockOptionsOpen(true)}
              placeholder="代码 / 名称 / 板块"
            />
          </label>
          {watchStockOptionsOpen && watchStockOptions.length ? (
            <div className="position-options">
              {watchStockOptions.map((option) => (
                <button key={option.symbol || option.code} onMouseDown={(event) => event.preventDefault()} onClick={() => selectWatchOption(option)} type="button">
                  <strong>{option.name}</strong>
                  <span>{option.code} · {option.sector || "--"} · {option.symbol}</span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
        {query.trim() ? (
          <button className="watch-add-button" onClick={addManualWatch} type="button">
            <Plus size={14} />
            加关注
          </button>
        ) : null}
        {watchAddStatus ? <small className="watch-add-status">{watchAddStatus}</small> : null}
      </section>

      <section className="watch-table-panel">
        <header>
          <h2>
            <Star size={16} />
            标的状态
          </h2>
          <span>{loading ? "同步中" : `${rows.length} 条`}</span>
        </header>
        {rows.length ? (
          <div className="watch-table">
            {rows.map((item) => (
              <article className={item.signal ? "active" : ""} key={`${item.list}-${item.code}`} onClick={() => setDetailItem(item)} onKeyDown={(event) => { if (event.key === "Enter") setDetailItem(item); }} role="button" tabIndex={0}>
                <div className="watch-stock">
                  <strong>{item.name}</strong>
                  <span>{item.code} · {watchSectorText(item)}</span>
                </div>
                <StatusPill item={item} />
                <TradeMarkPill item={item} />
                <div className="watch-values">
                  <small>评分</small>
                  <b>{item.signal?.score ?? (item.candidate ? Math.round(Number(item.candidate.adjusted_score ?? item.candidate.candidate_score ?? 0)) : "--")}</b>
                </div>
                <div className="watch-values">
                  <small>表现</small>
                  <b className={trendClass(watchPerformancePct(item))}>
                    {formatWatchPerformance(item)}
                  </b>
                </div>
                <IntradayChart item={item} />
                <div className="watch-state-detail">
                  <p>{statusText(item)}</p>
                  <div className="watch-tags">
                    {statusTags(item).map((tag) => (
                      <span className={tag.tone} key={`${item.code}-${tag.text}`}>
                        {tag.icon}
                        {tag.text}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="watch-actions">
                  {item.list === "watchlist" ? (
                    <button onClick={(event) => { event.stopPropagation(); move(item.code, "blocklist"); }} type="button">
                      <EyeOff size={14} />
                      屏蔽
                    </button>
                  ) : (
                    <button onClick={(event) => { event.stopPropagation(); move(item.code, "watchlist"); }} type="button">
                      <Heart size={14} />
                      关注
                    </button>
                  )}
                  <button onClick={(event) => { event.stopPropagation(); mode === "bought" && item.tradeMark ? clearTradeMark(item.code) : remove(item.code, item.list, Boolean(item.tradeMark)); }} type="button">
                    <Trash2 size={14} />
                    {mode === "bought" && item.tradeMark ? "取消标记" : "移除"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="empty">暂无匹配标的</div>
        )}
      </section>
      {detailItem ? (
        <WatchDetailDrawer
          item={detailItem}
          detail={detailPayload}
          kline={detailKline}
          aiReview={detailReview}
          openClawReview={detailOpenClaw}
          openClawLoading={openClawLoading}
          loading={detailLoading}
          onClose={() => setDetailItem(null)}
          onMove={async (code, list) => {
            await move(code, list);
            setDetailItem((current) => current && current.code === code ? { ...current, list } : current);
          }}
          onRemove={async (code, list, hasTradeMark) => {
            await remove(code, list, hasTradeMark);
            setDetailItem(null);
          }}
          onMark={markTrade}
          onOpenClawReview={runOpenClawReview}
        />
      ) : null}
    </main>
  );
}

function WatchDetailDrawer({
  item,
  detail,
  kline,
  aiReview,
  openClawReview,
  openClawLoading,
  loading,
  onClose,
  onMove,
  onRemove,
  onMark,
  onOpenClawReview,
}: {
  item: ManagedCode;
  detail: RadarDetailPayload | null;
  kline: KlinePayload | null;
  aiReview: AiTradeReviewPayload | null;
  openClawReview: OpenClawReviewPayload | null;
  openClawLoading: boolean;
  loading: boolean;
  onClose: () => void;
  onMove: (code: string, list: "watchlist" | "blocklist") => void;
  onRemove: (code: string, list: "watchlist" | "blocklist", hasTradeMark?: boolean) => void;
  onMark: (code: string, mark: TradeMark) => void;
  onOpenClawReview: (code: string) => void;
}) {
  const signal = detail?.signal || item.signal;
  const candidate = detail?.candidate || item.candidate;
  const track = detail?.track || item.track;
  const quotePrice = currentWatchPrice({ ...item, kline: kline || item.kline });
  const score = signal?.score ?? (candidate ? Math.round(Number(candidate.adjusted_score ?? candidate.candidate_score ?? 0)) : "--");
  const sector = item.sector || candidate?.sector || signal?.sector || "--";
  const action = aiReview?.recommendation?.action || (item.position ? "持仓复核" : item.tradeMark === "bought" ? "已买复核" : "观察");
  const entry = aiReview?.recommendation?.entry || (quotePrice ? `${(quotePrice * 0.992).toFixed(2)}-${quotePrice.toFixed(2)}` : "--");
  const stop = aiReview?.recommendation?.stop || (quotePrice ? (quotePrice * 0.975).toFixed(2) : "--");
  const watch = aiReview?.recommendation?.watch || (quotePrice ? (quotePrice * 1.018).toFixed(2) : "--");

  return (
    <div className="drawer-layer" role="dialog" aria-modal="true" aria-label="标的状态详情">
      <button className="drawer-backdrop" onClick={onClose} type="button" />
      <aside className="detail-drawer">
        <header>
          <div>
            <span>标的状态详情</span>
            <h3>{item.name}</h3>
            <p>{item.code} · {sector}</p>
          </div>
          <button onClick={onClose} type="button">关闭</button>
        </header>

        <section className="drawer-score">
          <div>
            <small>评分</small>
            <strong>{score}</strong>
          </div>
          <StatusPill item={item} />
        </section>

        <section className="drawer-actions">
          {item.list === "watchlist" ? (
            <button onClick={() => onMove(item.code, "blocklist")} type="button">屏蔽</button>
          ) : (
            <button onClick={() => onMove(item.code, "watchlist")} type="button">加入关注</button>
          )}
          <button className="danger" onClick={() => onRemove(item.code, item.list, Boolean(item.tradeMark))} type="button">移除</button>
        </section>

        <WatchOpenClawPanel review={openClawReview} loading={openClawLoading} onRun={() => onOpenClawReview(item.code)} />

        <section className={`trade-decision ${aiReview?.recommendation?.bias || "watch"}`}>
          <header>
            <div>
              <span>系统量价参考</span>
              <strong>{loading && !aiReview ? "分析中" : action}</strong>
            </div>
            <b>{item.tradeMark ? tradeMarkText(item.tradeMark) : item.position ? "持仓" : "未标记"}</b>
          </header>
          <p>{aiReview?.recommendation?.reason || aiReview?.summary || statusText(item)}</p>
          <div className="decision-actions">
            <button className={item.tradeMark === "bought" ? "active" : ""} onClick={() => onMark(item.code, "bought")} type="button">已买入</button>
            <button className={item.tradeMark === "wait_pullback" ? "active" : ""} onClick={() => onMark(item.code, "wait_pullback")} type="button">等回踩</button>
            <button className={item.tradeMark === "passed" ? "active danger" : "danger"} onClick={() => onMark(item.code, "passed")} type="button">已放弃</button>
          </div>
          <div className="decision-grid">
            <DetailMetricCell label="买入区" value={entry} />
            <DetailMetricCell label="止损位" value={stop} tone="down" />
            <DetailMetricCell label="观察位" value={watch} />
            <DetailMetricCell label="表现" value={formatWatchPerformance(item)} tone={trendClass(watchPerformancePct(item))} />
          </div>
          <WatchAiEvidence review={aiReview} loading={loading} />
        </section>

        <WatchDrawerIntradayChart kline={kline || item.kline || null} loading={loading} />

        <section className="drawer-metrics">
          <DetailMetricCell label="现价" value={quotePrice ? quotePrice.toFixed(2) : "--"} />
          <DetailMetricCell label="涨幅" value={formatPct(watchPerformancePct(item))} tone={trendClass(watchPerformancePct(item))} />
          {candidate ? <DetailMetricCell label="涨速" value={formatPct(candidate.rise_speed_pct || 0)} tone={trendClass(candidate.rise_speed_pct || 0)} /> : null}
          {candidate ? <DetailMetricCell label="2m额" value={formatMoney(candidate.min2_amount || 0)} /> : null}
          {signal ? <DetailMetricCell label="1m" value={formatPct(signal.rise_1m_pct || 0)} tone={trendClass(signal.rise_1m_pct || 0)} /> : null}
          {signal ? <DetailMetricCell label="1m额" value={formatMoney(signal.turnover_1m || 0)} /> : null}
          {track ? <DetailMetricCell label="跟踪" value={formatAge(track.age_sec)} /> : null}
          {item.position ? <DetailMetricCell label="持仓" value={`${item.position.shares}股`} /> : null}
        </section>

        <section className="drawer-section">
          <h4>当前状态</h4>
          {loading ? <small className="drawer-loading">正在同步详情...</small> : null}
          <p>{statusText(item)}</p>
          <div className="drawer-tags">
            {statusTags(item).map((tag) => (
              <span className={tag.tone === "risk" ? "risk" : ""} key={`${item.code}-${tag.text}`}>
                {tag.text}
              </span>
            ))}
          </div>
        </section>

        {track ? (
          <section className="drawer-section">
            <h4>触发后跟踪</h4>
            <div className="drawer-track">
              <DetailMetricCell label="现收益" value={formatPct(track.current_return_pct)} tone={trendClass(track.current_return_pct)} />
              <DetailMetricCell label="最高" value={formatPct(track.max_return_pct)} tone={trendClass(track.max_return_pct)} />
              <DetailMetricCell label="最低" value={formatPct(track.min_return_pct)} tone={trendClass(track.min_return_pct)} />
              <DetailMetricCell label="时长" value={formatAge(track.age_sec)} />
            </div>
          </section>
        ) : null}
      </aside>
    </div>
  );
}

function WatchOpenClawPanel({ review, loading, onRun }: { review: OpenClawReviewPayload | null; loading: boolean; onRun: () => void }) {
  const decision = review?.decision;
  const tone = decision?.risk_level === "high" ? "negative" : decision?.risk_level === "caution" ? "neutral" : "positive";
  return (
    <section className={`openclaw-decision ${tone}`}>
      <header>
        <div>
          <span>OpenClaw 策略复核</span>
          <strong>{loading ? "分析中" : decision ? `${openClawActionText(decision.action)} · ${decision.confidence}%` : "未复核"}</strong>
        </div>
        <button disabled={loading} onClick={onRun} type="button">{loading ? "等待" : "发送给 OpenClaw"}</button>
      </header>
      {review && !review.available ? <p className="risk">{review.error || "OpenClaw 暂不可用"}</p> : null}
      {decision ? (
        <>
          <p>{decision.summary}</p>
          <div className="decision-grid">
            <DetailMetricCell label="动作" value={openClawActionText(decision.action)} />
            <DetailMetricCell label="买入/加仓" value={decision.entry || "--"} />
            <DetailMetricCell label="止损" value={decision.stop || "--"} tone="down" />
            <DetailMetricCell label="观察" value={decision.watch || "--"} />
          </div>
          {decision.position_advice ? <p className="openclaw-advice">{decision.position_advice}</p> : null}
          <div className="decision-notes">
            {decision.reasons.slice(0, 3).map((text) => <span key={text}>{text}</span>)}
            {decision.risks.slice(0, 2).map((text) => <span className="risk" key={text}>{text}</span>)}
          </div>
        </>
      ) : (
        <p>把当前标的、关注状态和持仓上下文发送给本地 OpenClaw，返回独立策略判断。</p>
      )}
    </section>
  );
}

function WatchAiEvidence({ review, loading }: { review: AiTradeReviewPayload | null; loading: boolean }) {
  const points = review?.points?.length ? review.points : [];
  if (!points.length) return <small className="ai-evidence">{loading ? "资讯复核中..." : "资讯复核：暂无明显依据"}</small>;
  return (
    <details className="ai-evidence">
      <summary>
        <span>资讯复核</span>
        <b>{review?.summary || "无明显负反馈"}</b>
      </summary>
      <em>{review?.source || "mx-search"}</em>
      {points.slice(0, 3).map((point) => <p key={point}>{point}</p>)}
    </details>
  );
}

function WatchDrawerIntradayChart({ kline, loading }: { kline: KlinePayload | null; loading: boolean }) {
  const bars = kline?.bars?.length ? downsampleBars(kline.bars.slice(-120), 96) : [];
  if (!bars.length) {
    return (
      <section className="drawer-intraday empty-chart">
        <div>
          <strong>分时走势</strong>
          <span>{loading ? "加载中" : kline?.error || "暂无分时数据"}</span>
        </div>
      </section>
    );
  }
  const closes = bars.map((bar) => bar.close);
  const avgLine = movingAverage(closes, 5);
  const prevClose = bars[0].prev_close || bars[0].open;
  const min = Math.min(prevClose, ...bars.map((bar) => bar.low));
  const max = Math.max(prevClose, ...bars.map((bar) => bar.high));
  const range = Math.max(max - min, 0.01);
  const maxVolume = Math.max(...bars.map((bar) => bar.volume), 1);
  const last = bars[bars.length - 1];
  const tone = last.close >= prevClose ? "up" : "down";
  const priceLine = bars.map((bar, index) => `${drawerChartX(index, bars.length).toFixed(1)},${drawerChartY(bar.close, min, range).toFixed(1)}`).join(" ");
  const avgPoints = avgLine.map((value, index) => `${drawerChartX(index, avgLine.length).toFixed(1)},${drawerChartY(value, min, range).toFixed(1)}`).join(" ");
  const prevY = drawerChartY(prevClose, min, range);
  return (
    <section className={`drawer-intraday ${tone}`}>
      <div>
        <strong>分时走势</strong>
        <span>{kline?.date || "--"} · {bars.length}m · {formatPct(percentMove(last.close, prevClose))}</span>
      </div>
      <svg viewBox="0 0 360 150" role="img" aria-label="分时走势图">
        <line className="base" x1="14" x2="346" y1={prevY} y2={prevY} />
        {bars.map((bar, index) => {
          const height = Math.max(1.5, (bar.volume / maxVolume) * 28);
          const x = drawerChartX(index, bars.length);
          return <rect className="volume" key={`${bar.ts}-${index}`} x={x - 1.4} y={136 - height} width="2.8" height={height} rx="0.8" />;
        })}
        <polyline className="price" points={priceLine} />
        <polyline className="avg" points={avgPoints} />
      </svg>
    </section>
  );
}

function DetailMetricCell({ label, value, tone = "flat" }: { label: string; value: string | number; tone?: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </div>
  );
}

function tradeMarkText(mark: TradeMark): string {
  if (mark === "bought") return "已买";
  if (mark === "wait_pullback") return "等回踩";
  return "已放弃";
}

function openClawActionText(action?: string): string {
  if (action === "hold") return "持有";
  if (action === "add") return "加仓";
  if (action === "reduce") return "减仓";
  if (action === "sell") return "卖出";
  if (action === "watch") return "观察";
  return "--";
}

function drawerChartX(index: number, length: number): number {
  return 14 + index * (332 / Math.max(length - 1, 1));
}

function drawerChartY(value: number, min: number, range: number): number {
  return 104 - ((value - min) / range) * 86;
}

function Metric({ title, value, icon, detail, extra, detailTone = "flat", variant = "" }: { title: string; value: string | number; icon: ReactNode; detail?: string; extra?: string; detailTone?: string; variant?: string }) {
  return (
    <article className={variant}>
      <span>{icon}</span>
      <small>{title}</small>
      <strong>{value}</strong>
      {detail || extra ? (
        <div className="metric-detail">
          {detail ? <em className={detailTone}>{detail}</em> : null}
          {extra ? <em>{extra}</em> : null}
        </div>
      ) : null}
    </article>
  );
}

function PositionCell({ label, value, tone = "flat" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="position-cell">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </div>
  );
}

function PositionOpenClawReview({ review }: { review: OpenClawReviewPayload }) {
  const decision = review.decision;
  if (!review.available || !decision) {
    return <div className="position-openclaw-review risk">{review.error || "OpenClaw 暂不可用"}</div>;
  }
  return (
    <div className={`position-openclaw-review ${decision.risk_level === "high" ? "risk" : decision.risk_level === "caution" ? "caution" : ""}`}>
      <header>
        <span>OpenClaw持仓复盘</span>
        <strong>{openClawActionText(decision.action)} · {decision.confidence}%</strong>
      </header>
      <p>{decision.position_advice || decision.summary}</p>
      <div>
        <em>观察：{decision.watch || "--"}</em>
        <em>止损：{decision.stop || "--"}</em>
      </div>
    </div>
  );
}

function StatusPill({ item }: { item: ManagedCode }) {
  if (item.position) return <em className="trade bought">持仓中</em>;
  if (item.tradeMark === "bought") return <em className="trade bought">已买</em>;
  if (item.signal) return <em className="hot">{item.signal.grade}级异动</em>;
  if (item.candidate?.quality_level === "strong") return <em className="hot">强关注</em>;
  if (item.candidate) return <em>候选中</em>;
  if (item.track) return <em>跟踪中</em>;
  if (item.notification) return <em>已提醒</em>;
  return <em className="quiet">等待</em>;
}

function TradeMarkPill({ item }: { item: ManagedCode }) {
  const mark = item.tradeMark;
  if (!mark && item.position) return <em className="trade bought">已持仓</em>;
  if (!mark) return <em className="quiet">未标记</em>;
  if (mark === "bought") return <em className="trade bought">已买</em>;
  if (mark === "wait_pullback") return <em className="trade wait">等回踩</em>;
  return <em className="trade passed">已放弃</em>;
}

function IntradayChart({ item }: { item: ManagedCode }) {
  const bars = item.kline?.bars?.length ? item.kline.bars.slice(-90) : [];
  if (bars.length) return <RealIntradayChart bars={bars} error={item.kline?.error} />;
  const series = priceSeries(item);
  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = Math.max(max - min, 0.01);
  const points = series.map((value, index) => {
    return { value, index };
  });
  const change = series[series.length - 1] - series[0];
  const tone = change >= 0 ? "up" : "down";

  return (
    <div className={`intraday-chart ${tone}`} aria-label="分时图">
      <svg viewBox="0 0 180 62" role="img">
        <line className="base" x1="8" x2="172" y1="38" y2="38" />
        {points.slice(1).map((point, index) => {
          const previous = points[index];
          const x = 8 + index * (164 / Math.max(points.length - 2, 1));
          const y = 53 - ((point.value - min) / range) * 42;
          const previousY = 53 - ((previous.value - min) / range) * 42;
          const high = Math.min(y, previousY) - 4;
          const low = Math.max(y, previousY) + 4;
          const bodyTop = Math.min(y, previousY);
          const bodyHeight = Math.max(Math.abs(y - previousY), 3);
          return (
            <g className={point.value >= previous.value ? "rise" : "fall"} key={`${x}-${y}`}>
              <line x1={x} x2={x} y1={Math.max(7, high)} y2={Math.min(56, low)} />
              <rect x={x - 3.2} y={bodyTop} width="6.4" height={bodyHeight} rx="1.2" />
            </g>
          );
        })}
      </svg>
      <span>估算分时 {formatPct(percentMove(series[series.length - 1], series[0]))}</span>
    </div>
  );
}

function RealIntradayChart({ bars, error }: { bars: KlineBar[]; error?: string }) {
  const sampled = downsampleBars(bars, 80);
  const closes = sampled.map((item) => item.close);
  const avgLine = movingAverage(closes, 5);
  const prevClose = sampled[0].prev_close || sampled[0].open;
  const min = Math.min(prevClose, ...sampled.map((item) => item.low));
  const max = Math.max(prevClose, ...sampled.map((item) => item.high));
  const range = Math.max(max - min, 0.01);
  const maxVolume = Math.max(...sampled.map((item) => item.volume), 1);
  const closeLine = sampled
    .map((bar, index) => {
      const x = 8 + index * (164 / Math.max(sampled.length - 1, 1));
      const y = priceY(bar.close, min, range);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const avgPoints = avgLine
    .map((value, index) => {
      const x = 8 + index * (164 / Math.max(avgLine.length - 1, 1));
      return `${x.toFixed(1)},${priceY(value, min, range).toFixed(1)}`;
    })
    .join(" ");
  const yPrev = priceY(prevClose, min, range);
  const last = sampled[sampled.length - 1];
  const tone = last.close >= prevClose ? "up" : "down";

  return (
    <div className={`intraday-chart real ${tone}`} title={error || `真实分时 ${bars.length}根`}>
      <svg viewBox="0 0 180 72" role="img" aria-label="真实分时图">
        <line className="base" x1="8" x2="172" y1={yPrev} y2={yPrev} />
        {sampled.map((bar, index) => {
          const x = 8 + index * (164 / Math.max(sampled.length - 1, 1));
          const height = Math.max(1, (bar.volume / maxVolume) * 13);
          return (
            <rect className="volume" key={`${bar.ts}-${index}`} x={x - 0.9} y={66 - height} width="1.8" height={height} rx="0.6" />
          );
        })}
        <polyline points={closeLine} />
        <polyline className="avg" points={avgPoints} />
      </svg>
      <span>分时 {bars.length}m · {formatPct(percentMove(last.close, prevClose))}</span>
    </div>
  );
}

function downsampleBars(bars: KlineBar[], maxCount: number): KlineBar[] {
  if (bars.length <= maxCount) return bars;
  const step = Math.ceil(bars.length / maxCount);
  const sampled: KlineBar[] = [];
  for (let index = 0; index < bars.length; index += step) {
    const chunk = bars.slice(index, index + step);
    sampled.push({
      ts: chunk[chunk.length - 1].ts,
      open: chunk[0].open,
      close: chunk[chunk.length - 1].close,
      high: Math.max(...chunk.map((item) => item.high)),
      low: Math.min(...chunk.map((item) => item.low)),
      volume: chunk.reduce((sum, item) => sum + item.volume, 0),
      amount: chunk.reduce((sum, item) => sum + item.amount, 0),
      prev_close: chunk[0].prev_close,
    });
  }
  return sampled;
}

function priceY(value: number, min: number, range: number): number {
  return 47 - ((value - min) / range) * 38;
}

function movingAverage(values: number[], windowSize: number): number[] {
  return values.map((_, index) => {
    const start = Math.max(0, index - windowSize + 1);
    const slice = values.slice(start, index + 1);
    return slice.reduce((sum, value) => sum + value, 0) / slice.length;
  });
}

type PositionRow = {
  code: string;
  name: string;
  sector: string;
  buyPrice: number;
  shares: number;
  currentPrice: number;
  marketValue: number;
  cost: number;
  profit: number;
  returnPct: number;
};

function buildPositionRows(positions: Position[], rows: ManagedCode[], candidates: Candidate[], signals: Signal[], klines: Record<string, KlinePayload>, stockMeta: Record<string, StockOption>, quotes: Record<string, MarketQuote>): PositionRow[] {
  return positions.map((position) => {
    const managed = rows.find((item) => item.code === position.code);
    const candidate = candidates.find((item) => item.code === position.code);
    const signal = signals.find((item) => item.code === position.code);
    const meta = stockMeta[position.code];
    const quote = quotes[position.code];
    const bars = klines[position.code]?.bars || [];
    const lastBar = bars[bars.length - 1];
    const currentPrice = Number(quote?.price || signal?.price || candidate?.price || managed?.track?.current_price || lastBar?.close || position.buy_price || 0);
    const cost = position.buy_price * position.shares;
    const marketValue = currentPrice * position.shares;
    const profit = marketValue - cost;
    return {
      code: position.code,
      name: managed?.name || candidate?.name || signal?.name || meta?.name || position.name || position.code,
      sector: managed?.sector || candidate?.sector || signal?.sector || meta?.sector || position.sector || "--",
      buyPrice: position.buy_price,
      shares: position.shares,
      currentPrice,
      marketValue,
      cost,
      profit,
      returnPct: cost ? (profit / cost) * 100 : 0,
    };
  });
}

function summarizePositions(rows: PositionRow[]): { cost: number; marketValue: number; profit: number } {
  return rows.reduce(
    (sum, row) => ({
      cost: sum.cost + row.cost,
      marketValue: sum.marketValue + row.marketValue,
      profit: sum.profit + row.profit,
    }),
    { cost: 0, marketValue: 0, profit: 0 },
  );
}

function watchPerformancePct(item: ManagedCode): number {
  if (item.track) return item.track.current_return_pct;
  if (item.signal) return item.signal.change_pct;
  if (item.candidate) return item.candidate.change_pct || 0;
  if (item.position) {
    const current = currentWatchPrice(item);
    return current ? percentMove(current, item.position.buy_price) : 0;
  }
  return 0;
}

function formatWatchPerformance(item: ManagedCode): string {
  if (item.track || item.signal || item.candidate) return formatPct(watchPerformancePct(item));
  if (!item.position) return "--";
  const current = currentWatchPrice(item);
  return current ? formatPct(percentMove(current, item.position.buy_price)) : "持仓中";
}

function watchSectorText(item: ManagedCode): string {
  if (item.sector && item.sector !== "--") return item.sector;
  if (item.position) return "持仓";
  return "--";
}

function currentWatchPrice(item: ManagedCode): number {
  const bars = item.kline?.bars || [];
  const lastBar = bars[bars.length - 1];
  return Number(item.quote?.price || item.signal?.price || item.candidate?.price || item.track?.current_price || lastBar?.close || 0);
}

function normalizeStockCode(value: string): string {
  const match = value.trim().match(/\b\d{6}\b/);
  return match ? match[0] : value.trim();
}

function formatSignedMoney(value: number): string {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${Number(value || 0).toFixed(2)}元`;
}

function openingReviewWindowState(date: Date): { inWindow: boolean; label: string } {
  const day = date.getDay();
  if (day === 0 || day === 6) return { inWindow: false, label: "等待交易日 09:25" };
  const minutes = date.getHours() * 60 + date.getMinutes();
  const start = 9 * 60 + 25;
  const end = 10 * 60;
  if (minutes < start) return { inWindow: false, label: "等待 09:25 自动分析" };
  if (minutes <= end) return { inWindow: true, label: "开盘自动监控中" };
  return { inWindow: false, label: "今日开盘自动分析结束" };
}

function syncLocalPreferences(preferences: PreferencePayload["preferences"]): void {
  localStorage.setItem(WATCHLIST_KEY, JSON.stringify(preferences.watchlist || []));
  localStorage.setItem(BLOCKLIST_KEY, JSON.stringify(preferences.blocklist || []));
}

function syncLocalTradeMarks(marks: TradeMarksPayload["marks"]): void {
  const simpleMarks = Object.fromEntries(Object.entries(marks).map(([code, value]) => [code, value.mark]));
  localStorage.setItem(TRADE_MARKS_KEY, JSON.stringify(simpleMarks));
}

function buildManagedRows(
  preferences: PreferencePayload["preferences"],
  candidates: Candidate[],
  signals: Signal[],
  tracks: Track[],
  notifications: NotificationItem[],
  klines: Record<string, KlinePayload>,
  tradeMarks: TradeMarksPayload["marks"],
  stockMeta: Record<string, StockOption>,
  positions: Position[],
  quotes: Record<string, MarketQuote>,
): ManagedCode[] {
  const create = (code: string, list: "watchlist" | "blocklist") => {
    const candidate = candidates.find((item) => item.code === code);
    const signal = signals.find((item) => item.code === code);
    const track = tracks.find((item) => item.code === code);
    const notification = notifications.find((item) => item.code === code);
    const meta = stockMeta[code];
    const position = positions.find((item) => item.code === code);
    const quote = quotes[code];
    return {
      code,
      name: candidate?.name || signal?.name || track?.name || notification?.name || quote?.name || meta?.name || position?.name || code,
      sector: candidate?.sector || signal?.sector || meta?.sector || position?.sector || "--",
      list,
      candidate,
      signal,
      track,
      notification,
      kline: klines[code],
      tradeMark: tradeMarks[code]?.mark,
      position,
      quote,
    };
  };
  const rows = [
    ...preferences.watchlist.map((code) => create(code, "watchlist")),
    ...preferences.blocklist.map((code) => create(code, "blocklist")),
  ];
  for (const code of Object.keys(tradeMarks)) {
    if (!rows.some((item) => item.code === code)) rows.push(create(code, "watchlist"));
  }
  for (const position of positions) {
    if (!rows.some((item) => item.code === position.code)) rows.push(create(position.code, "watchlist"));
  }
  return rows;
}

function rowPriority(item: ManagedCode): number {
  return (item.position ? 1500 : 0) + (item.tradeMark === "bought" ? 1400 : 0) + (item.signal ? 1000 : 0) + (item.candidate ? 500 : 0) + (item.track ? 120 : 0) + (item.notification ? item.notification.ts / 100000000 : 0);
}

function matchWatchMode(item: ManagedCode, mode: WatchMode): boolean {
  if (mode === "all") return true;
  if (mode === "positions") return Boolean(item.position);
  if (mode === "bought") return item.tradeMark === "bought";
  return item.list === mode;
}

function priceSeries(item: ManagedCode): number[] {
  if (item.signal?.sparkline?.length) return item.signal.sparkline.slice(-12).map(Number).filter((value) => value > 0);
  if (item.track?.trigger_price && item.track.current_price) {
    const start = item.track.trigger_price;
    const high = start * (1 + item.track.max_return_pct / 100);
    const low = start * (1 + item.track.min_return_pct / 100);
    const end = item.track.current_price;
    return smoothSeries([start, low, start * 1.002, high, end]);
  }
  const candidate = item.candidate;
  if (candidate?.price) {
    const start = candidate.prev_close || candidate.price / (1 + Number(candidate.change_pct || 0) / 100);
    const lift = Number(candidate.rise_speed_pct || 0) / 100;
    return smoothSeries([start, start * (1 + lift * 0.25), candidate.price * 0.996, candidate.price * 1.002, candidate.price]);
  }
  if (item.signal?.price) {
    const start = item.signal.price / (1 + Number(item.signal.change_pct || 0) / 100);
    return smoothSeries([start, item.signal.price * 0.994, item.signal.price * 1.003, item.signal.price]);
  }
  return [1, 1, 1, 1, 1];
}

function smoothSeries(points: number[]): number[] {
  const output: number[] = [];
  for (let index = 0; index < points.length - 1; index += 1) {
    const start = points[index];
    const end = points[index + 1];
    output.push(start, start + (end - start) * 0.45);
  }
  output.push(points[points.length - 1]);
  return output.filter((value) => Number.isFinite(value) && value > 0);
}

function percentMove(current: number, base: number): number {
  if (!base) return 0;
  return (current / base - 1) * 100;
}

function rowState(item: ManagedCode): "active" | "alerted" | "idle" {
  if (item.signal || item.candidate || item.track || item.position) return "active";
  if (item.notification) return "alerted";
  return "idle";
}

function statusText(item: ManagedCode): string {
  if (item.signal) return `${item.signal.sector} · 1m ${formatPct(item.signal.rise_1m_pct)} · ${formatMoney(item.signal.turnover_1m)}`;
  if (item.candidate) return item.candidate.explanation || `${item.candidate.sector || "--"} · ${formatMoney(item.candidate.min2_amount || 0)}`;
  if (item.track) return `跟踪 ${formatAge(item.track.age_sec)} · 高 ${formatPct(item.track.max_return_pct)} · 低 ${formatPct(item.track.min_return_pct)}`;
  if (item.position) return `持仓 ${item.position.shares}股 · 成本 ${item.position.buy_price.toFixed(2)} · ${formatWatchPerformance(item)}`;
  if (item.notification) return `${new Date(item.notification.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })} · ${item.notification.title}`;
  return "未进入候选或实时异动";
}

function statusTags(item: ManagedCode): Array<{ text: string; tone: string; icon?: ReactNode }> {
  const tags: Array<{ text: string; tone: string; icon?: ReactNode }> = [];
  if (item.signal) {
    tags.push({ text: `${item.signal.grade}级`, tone: "hot", icon: <TrendingUp size={12} /> });
    tags.push({ text: `1m ${formatPct(item.signal.rise_1m_pct)}`, tone: trendClass(item.signal.rise_1m_pct) });
  }
  if (item.candidate) {
    const reasons = item.candidate.candidate_reasons || [];
    const risks = [...(item.candidate.risk_flags || []), ...(item.candidate.miss_reasons || [])];
    if (item.candidate.hot_money_role) tags.push({ text: item.candidate.hot_money_role, tone: "good", icon: <Target size={12} /> });
    if (item.candidate.market_mood) tags.push({ text: `情绪${item.candidate.market_mood}`, tone: item.candidate.market_mood === "退潮" ? "risk" : "info" });
    if (item.candidate.theme_rank && item.candidate.theme_rank < 99) tags.push({ text: `题材#${item.candidate.theme_rank}`, tone: "info" });
    if (item.candidate.buy_pattern) tags.push({ text: item.candidate.buy_pattern, tone: item.candidate.buy_pattern.includes("不") ? "risk" : "good" });
    for (const reason of reasons.slice(0, 3)) tags.push({ text: reason, tone: "good", icon: <Target size={12} /> });
    for (const risk of risks.slice(0, 2)) tags.push({ text: risk, tone: "risk", icon: <AlertTriangle size={12} /> });
  }
  if (item.track) {
    tags.push({ text: `跟踪 ${formatAge(item.track.age_sec)}`, tone: "info" });
    tags.push({ text: `高 ${formatPct(item.track.max_return_pct)}`, tone: trendClass(item.track.max_return_pct) });
  }
  if (item.position && !item.signal && !item.candidate && !item.track) {
    tags.push({ text: "当前持仓", tone: "info", icon: <Target size={12} /> });
    tags.push({ text: `成本 ${item.position.buy_price.toFixed(2)}`, tone: "quiet" });
  }
  if (item.notification) tags.push({ text: item.notification.kind, tone: "info", icon: <Bell size={12} /> });
  if (!tags.length) tags.push({ text: "等待触发", tone: "quiet" });
  return tags.slice(0, 7);
}
