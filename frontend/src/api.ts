import type { AiTradeReviewPayload, CandidatePayload, FocusAdvicePayload, FocusReviewRecord, FocusStrategyPayload, KlinePayload, LimitUpNextDayPayload, LimitUpOpenClawJobPayload, LimitUpPayload, LimitUpSystemReviewPayload, LimitUpTomorrowFocusPayload, MarketQuotesPayload, NotificationConfig, NotificationPayload, OpenClawReviewPayload, PositionsPayload, PreferencePayload, RadarDetailPayload, SnapshotPayload, StockLookupPayload, StockSearchPayload, TradeMark, TradeMarksPayload, TradeRecordsPayload } from "./types";

const API_BASE_STORAGE_KEY = "gupiao.apiBase";
const apiBase = import.meta.env.VITE_API_BASE || "";

export function getApiBase(): string {
  if (apiBase) return normalizeApiBase(apiBase);
  return normalizeApiBase(window.localStorage.getItem(API_BASE_STORAGE_KEY) || "");
}

export function setApiBase(value: string): string {
  const next = normalizeApiBase(value);
  if (next) window.localStorage.setItem(API_BASE_STORAGE_KEY, next);
  else window.localStorage.removeItem(API_BASE_STORAGE_KEY);
  return next;
}

export function normalizeApiBase(value: string): string {
  return String(value || "").trim().replace(/\/+$/, "");
}

export async function fetchSnapshot(): Promise<SnapshotPayload> {
  return fetchJson<SnapshotPayload>("/api/snapshot");
}

export async function fetchCandidates(): Promise<CandidatePayload> {
  return fetchJson<CandidatePayload>("/api/candidates");
}

export async function fetchRadarDetail(code: string): Promise<RadarDetailPayload> {
  return fetchJson<RadarDetailPayload>(`/api/radar/detail?code=${encodeURIComponent(code)}`);
}

export async function fetchKline(code: string, limit = 120): Promise<KlinePayload> {
  return fetchJson<KlinePayload>(`/api/market/kline?code=${encodeURIComponent(code)}&limit=${limit}`);
}

export async function fetchMarketQuotes(codes: string[]): Promise<MarketQuotesPayload> {
  return fetchJson<MarketQuotesPayload>(`/api/market/quotes?codes=${encodeURIComponent(codes.join(","))}`);
}

export async function searchStocks(query: string, limit = 20): Promise<StockSearchPayload> {
  return fetchJson<StockSearchPayload>(`/api/stocks/search?q=${encodeURIComponent(query)}&limit=${limit}`);
}

export async function lookupStocks(codes: string[]): Promise<StockLookupPayload> {
  return fetchJson<StockLookupPayload>(`/api/stocks/lookup?codes=${encodeURIComponent(codes.join(","))}`);
}

export async function fetchAiTradeReview(code: string): Promise<AiTradeReviewPayload> {
  return fetchJson<AiTradeReviewPayload>(`/api/ai/trade-review?code=${encodeURIComponent(code)}`);
}

export async function fetchOpenClawReview(code: string): Promise<OpenClawReviewPayload> {
  return fetchJson<OpenClawReviewPayload>(`/api/openclaw/review?code=${encodeURIComponent(code)}`);
}

export async function fetchNotifications(limit = 20): Promise<NotificationPayload> {
  return fetchJson<NotificationPayload>(`/api/notifications/recent?limit=${limit}`);
}

export async function fetchLimitUpState(force = false): Promise<LimitUpPayload> {
  return fetchJson<LimitUpPayload>(`/api/limit-up/state?force=${force ? "1" : "0"}`);
}

export async function refreshLimitUpState(notify = false): Promise<LimitUpPayload> {
  return fetchJson<LimitUpPayload>(`/api/limit-up/refresh?notify=${notify ? "1" : "0"}`);
}

export async function fetchLimitUpTomorrowFocus(notify = false): Promise<LimitUpTomorrowFocusPayload> {
  return fetchJson<LimitUpTomorrowFocusPayload>(`/api/limit-up/tomorrow-focus?notify=${notify ? "1" : "0"}`);
}

export async function reviewLimitUpTomorrowFocus(maxItems = 120, notify = false, timeout = 600): Promise<LimitUpTomorrowFocusPayload> {
  return fetchJson<LimitUpTomorrowFocusPayload>(`/api/limit-up/openclaw-review?max_items=${maxItems}&timeout=${timeout}&notify=${notify ? "1" : "0"}`);
}

export async function startLimitUpOpenClawReview(maxItems = 120, notify = false, timeout = 600): Promise<LimitUpOpenClawJobPayload> {
  return fetchJson<LimitUpOpenClawJobPayload>(`/api/limit-up/openclaw-review/start?max_items=${maxItems}&timeout=${timeout}&notify=${notify ? "1" : "0"}`);
}

export async function fetchLimitUpOpenClawReviewStatus(jobId: string): Promise<LimitUpOpenClawJobPayload> {
  return fetchJson<LimitUpOpenClawJobPayload>(`/api/limit-up/openclaw-review/status?job_id=${encodeURIComponent(jobId)}`);
}

export async function fetchLimitUpNextDayMonitor(notify = false): Promise<LimitUpNextDayPayload> {
  return fetchJson<LimitUpNextDayPayload>(`/api/limit-up/next-day-monitor?notify=${notify ? "1" : "0"}`);
}

export async function fetchLimitUpSystemReview(date = ""): Promise<LimitUpSystemReviewPayload> {
  return fetchJson<LimitUpSystemReviewPayload>(`/api/limit-up/system-review?date=${encodeURIComponent(date)}`);
}

export async function fetchFullHealth(): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>("/api/health/full");
}

export async function updateNotificationConfig(config: Partial<NotificationConfig>): Promise<NotificationPayload> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(config)) {
    if (value !== undefined) params.set(key, String(value));
  }
  return fetchJson<NotificationPayload>(`/api/notifications/config?${params.toString()}`);
}

export async function testNotification(): Promise<NotificationPayload> {
  return fetchJson<NotificationPayload>("/api/notifications/test");
}

export async function fetchPreferences(): Promise<PreferencePayload> {
  return fetchJson<PreferencePayload>("/api/preferences");
}

export async function fetchFocusAdvice(): Promise<FocusAdvicePayload> {
  return fetchJson<FocusAdvicePayload>("/api/focus/advice");
}

export async function fetchFocusStrategy(): Promise<FocusStrategyPayload> {
  return fetchJson<FocusStrategyPayload>("/api/focus/strategy");
}

export async function fetchFocusRecords(limit = 80, includeShadow = true): Promise<{ records: FocusReviewRecord[] }> {
  return fetchJson<{ records: FocusReviewRecord[] }>(`/api/focus/next-day?limit=${limit}&include_shadow=${includeShadow ? "true" : "false"}`);
}

export async function fetchTradeRecords(limit = 120): Promise<TradeRecordsPayload> {
  return fetchJson<TradeRecordsPayload>(`/api/trade-records?limit=${limit}`);
}

export async function addPreference(list: "watchlist" | "blocklist", code: string): Promise<PreferencePayload> {
  return fetchJson<PreferencePayload>(`/api/preferences/add?list=${list}&code=${encodeURIComponent(code)}`);
}

export async function removePreference(list: "watchlist" | "blocklist", code: string): Promise<PreferencePayload> {
  return fetchJson<PreferencePayload>(`/api/preferences/remove?list=${list}&code=${encodeURIComponent(code)}`);
}

export async function fetchTradeMarks(): Promise<TradeMarksPayload> {
  return fetchJson<TradeMarksPayload>("/api/trade-marks");
}

export async function setTradeMark(code: string, mark: TradeMark): Promise<TradeMarksPayload> {
  return fetchJson<TradeMarksPayload>(`/api/trade-marks/set?code=${encodeURIComponent(code)}&mark=${encodeURIComponent(mark)}`);
}

export async function removeTradeMark(code: string): Promise<TradeMarksPayload> {
  return fetchJson<TradeMarksPayload>(`/api/trade-marks/remove?code=${encodeURIComponent(code)}`);
}

export async function fetchPositions(): Promise<PositionsPayload> {
  return fetchJson<PositionsPayload>("/api/positions");
}

export async function upsertPosition(input: { code: string; name?: string; sector?: string; price: number; shares: number; source?: string }): Promise<PositionsPayload> {
  const params = new URLSearchParams({
    code: input.code,
    name: input.name || "",
    sector: input.sector || "",
    price: String(input.price),
    shares: String(input.shares),
    source: input.source || "",
  });
  return fetchJson<PositionsPayload>(`/api/positions/upsert?${params.toString()}`);
}

export async function removePosition(code: string): Promise<PositionsPayload> {
  return fetchJson<PositionsPayload>(`/api/positions/remove?code=${encodeURIComponent(code)}`);
}

export async function addTradeRecord(input: { code: string; name?: string; sector?: string; side: string; price: number; shares: number; reason?: string; source?: string }): Promise<TradeRecordsPayload> {
  const params = new URLSearchParams({
    code: input.code,
    name: input.name || "",
    sector: input.sector || "",
    side: input.side,
    price: String(input.price),
    shares: String(input.shares),
    reason: input.reason || "",
    source: input.source || "",
  });
  return fetchJson<TradeRecordsPayload>(`/api/trade-records/add?${params.toString()}`);
}

export async function sendPositionRiskNotification(input: { code: string; name?: string; action: string; price?: number; reason?: string }): Promise<{ notification: unknown }> {
  const params = new URLSearchParams({
    code: input.code,
    name: input.name || "",
    action: input.action,
    price: input.price ? input.price.toFixed(2) : "",
    reason: input.reason || "",
  });
  return fetchJson<{ notification: unknown }>(`/api/notifications/position-risk?${params.toString()}`);
}

export async function sendExecutionAlertNotification(input: { code: string; name?: string; action: string; price?: number; reason?: string }): Promise<{ notification: unknown }> {
  const params = new URLSearchParams({
    code: input.code,
    name: input.name || "",
    action: input.action,
    price: input.price ? input.price.toFixed(2) : "",
    reason: input.reason || "",
  });
  return fetchJson<{ notification: unknown }>(`/api/notifications/execution-alert?${params.toString()}`);
}

export function radarWebSocketUrl(): string {
  const explicit = import.meta.env.VITE_WS_URL;
  if (explicit) return explicit;

  const base = getApiBase() || window.location.origin;
  const url = new URL("/ws/radar", base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function limitUpWebSocketUrl(): string {
  const explicit = import.meta.env.VITE_LIMIT_UP_WS_URL;
  if (explicit) return explicit;

  const base = getApiBase() || window.location.origin;
  const url = new URL("/ws/limit-up", base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${getApiBase()}${path}`);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}
