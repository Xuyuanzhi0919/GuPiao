export type TrendGrade = "A" | "B" | "C" | string;

export interface RuntimeStatus {
  status?: string;
  data_age_sec?: number | null;
  client_count?: number;
  tick_count?: number;
  batch_count?: number;
  source?: string;
  session?: {
    code?: string;
    label?: string;
    is_live?: boolean;
    time?: string;
    date?: string;
  };
  upstream_health?: Record<string, unknown>;
}

export interface Signal {
  code: string;
  name: string;
  sector: string;
  grade: TrendGrade;
  score: number;
  price: number;
  change_pct: number;
  rise_1m_pct: number;
  rise_3m_pct?: number;
  turnover_1m: number;
  active_buy_ratio: number;
  volume_spike?: number;
  order_book_bias?: number;
  signal_age_sec?: number;
  quality_tags?: string[];
  reasons?: string[];
  sparkline?: number[];
}

export interface Candidate {
  code: string;
  name: string;
  sector?: string;
  board?: string;
  price?: number;
  prev_close?: number;
  auction_price?: number;
  auction_change_pct?: number | null;
  auction_amount?: number;
  auction_volume_ratio?: number;
  auction_source?: string;
  change_pct?: number;
  rise_speed_pct?: number;
  min2_amount?: number;
  active_buy_ratio?: number;
  turnover_rate?: number;
  adjusted_score?: number;
  candidate_score?: number;
  quality_level?: "strong" | "watch" | "caution" | string;
  quality_label?: string;
  explanation?: string;
  candidate_reasons?: string[];
  risk_flags?: string[];
  miss_reasons?: string[];
  market_mood?: string;
  emotion_score?: number;
  theme_rank?: number;
  theme_score?: number;
  hot_money_role?: string;
  leader_role?: string;
  leader_score?: number;
  market_height_rank?: number;
  theme_leader_rank?: number;
  limit_up?: boolean;
  limit_up_streak?: number;
  limit_up_threshold_pct?: number;
  first_limit_time?: string;
  last_limit_time?: string;
  limit_up_amount?: number;
  seal_amount?: number;
  open_board_count?: number;
  distance_to_limit_pct?: number;
  buy_pattern?: string;
  hot_money_tags?: string[];
  top_status?: string;
  top_age_sec?: number;
  top_reason?: string;
  cooldown_sec?: number;
}

export interface SectorHeat {
  sector: string;
  count: number;
  active_top?: number;
}

export interface Track {
  code: string;
  name: string;
  grade?: string;
  age_sec: number;
  trigger_price?: number;
  current_price?: number;
  current_return_pct: number;
  max_return_pct: number;
  min_return_pct: number;
}

export interface KlineBar {
  ts: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  amount: number;
  prev_close?: number;
}

export interface KlinePayload {
  code: string;
  symbol: string;
  date: string;
  source: string;
  bars: KlineBar[];
  error?: string;
}

export interface MarketQuote {
  code: string;
  name?: string;
  price: number;
  change_pct?: number;
  change?: number;
  open?: number;
  high?: number;
  low?: number;
  prev_close?: number;
  volume?: number;
  amount?: number;
  source?: string;
  ts?: number;
}

export interface MarketQuotesPayload {
  quotes: Record<string, MarketQuote>;
  source?: string;
  ts?: number;
  error?: string;
}

export interface SnapshotPayload {
  event?: string;
  signals?: Signal[];
  new_signals?: Signal[];
  tracked_alerts?: Track[];
  performance?: {
    total?: number;
    positive_rate?: number;
    by_grade?: Record<string, unknown>;
  };
  sector_heat?: Record<string, number>;
  runtime?: RuntimeStatus;
}

export interface CandidatePayload {
  candidates?: Candidate[];
  sector_heat?: SectorHeat[];
  hot_money?: {
    mood?: string;
    emotion_score?: number;
    strong_count?: number;
    high_count?: number;
    active_top?: number;
  };
  leader_pool?: {
    date?: string;
    limit_pool_count?: number;
    limit_pool_error?: string;
    market_height?: number;
    emotion?: {
      cycle?: string;
      score?: number;
      action?: string;
      position?: string;
      mode?: string;
      fail_rate?: number;
      open_board_count?: number;
      high_count?: number;
      second_count?: number;
      early_count?: number;
      mainline?: string;
      reason?: string;
    };
    leaders?: Array<{
      code?: string;
      name?: string;
      sector?: string;
      leader_role?: string;
      limit_up_streak?: number;
      leader_score?: number;
    }>;
    height_leaders?: Array<{
      code?: string;
      name?: string;
      sector?: string;
      limit_up_streak?: number;
      first_limit_time?: string;
      last_limit_time?: string;
      open_board_count?: number;
      seal_amount?: number;
    }>;
    limit_themes?: Array<{
      sector: string;
      limit_count: number;
      max_height: number;
      early_count: number;
      open_board_count: number;
      seal_amount: number;
      score: number;
      risk_flags?: string[];
      leader?: {
        code?: string;
        name?: string;
        limit_up_streak?: number;
        first_limit_time?: string;
      } | null;
      stocks?: Array<{
        code?: string;
        name?: string;
        limit_up_streak?: number;
        first_limit_time?: string;
        last_limit_time?: string;
        open_board_count?: number;
        seal_amount?: number;
        role?: string;
      }>;
    }>;
  };
  health?: {
    source?: string;
    filtered_count?: number;
    sector_heat?: SectorHeat[];
    filtered_reasons?: Record<string, number>;
  };
  error?: string;
}

export interface RadarDetailPayload {
  code: string;
  candidate?: Candidate | null;
  signal?: Signal | null;
  track?: Track | null;
  sector?: SectorHeat | null;
  same_sector_candidates?: Candidate[];
  runtime?: RuntimeStatus;
}

export interface AiTradeReviewPayload {
  code: string;
  name?: string;
  sector?: string;
  available: boolean;
  summary: string;
  points: string[];
  source: string;
  recommendation?: {
    action: string;
    entry: string;
    stop: string;
    watch: string;
    bias: "positive" | "negative" | "neutral" | string;
    reason: string;
    positive_hits?: string[];
    negative_hits?: string[];
  };
  query?: string;
  elapsed_ms?: number;
}

export interface OpenClawReviewPayload {
  code: string;
  name?: string;
  available: boolean;
  source: string;
  elapsed_ms?: number;
  error?: string;
  decision?: {
    action: "hold" | "add" | "reduce" | "sell" | "watch" | string;
    confidence: number;
    summary: string;
    entry: string;
    stop: string;
    watch: string;
    position_advice: string;
    risk_level: "normal" | "caution" | "high" | string;
    reasons: string[];
    risks: string[];
  } | null;
}

export interface NotificationItem {
  ts: number;
  kind: string;
  code: string;
  name: string;
  title: string;
  body: string;
  channel: string;
  sent: boolean;
  error?: string;
  elapsed_ms?: number;
  target?: string;
}

export interface NotificationPayload {
  notifications: NotificationItem[];
  status: {
    enabled: boolean;
    bark_configured: boolean;
    bark_url?: string;
    omni_bark_configured?: boolean;
    omni_bark_token?: string;
    omni_bark_channel_id?: string;
    backup_bark_count?: number;
    cooldown_sec: number;
    failed_retry_sec?: number;
    recent_count: number;
    cooldown_key_count?: number;
    sector_pulse_threshold?: number;
    notification_health?: {
      sample_count: number;
      success_count: number;
      failure_count: number;
      success_rate: number;
      avg_elapsed_ms: number;
      consecutive_failures: number;
      last_error: string;
      last_error_ts: number;
      last_success_ts: number;
    };
    config?: NotificationConfig;
    rules?: Array<{
      key: string;
      label: string;
      enabled: boolean;
      description: string;
    }>;
  };
  test?: NotificationItem;
}

export interface NotificationConfig {
  enabled: boolean;
  signal_a_enabled: boolean;
  focus_strong_enabled: boolean;
  watchlist_signal_enabled: boolean;
  sector_pulse_enabled: boolean;
  execution_alert_enabled: boolean;
  limit_up_signal_enabled: boolean;
  limit_up_focus_enabled: boolean;
  next_day_buy_enabled: boolean;
  next_day_risk_enabled: boolean;
  cooldown_sec: number;
  failed_retry_sec?: number;
  sector_pulse_threshold: number;
  bark_url?: string;
  backup_bark_urls?: string;
  omni_bark_token?: string;
  omni_bark_channel_id?: string;
  omni_bark_sender?: string;
  omni_bark_api_base?: string;
  critical_sound?: string;
}

export interface LimitUpStock {
  code: string;
  name: string;
  sector?: string;
  price?: number;
  change_pct?: number;
  amount?: number;
  float_market_value?: number;
  turnover_rate?: number;
  streak?: number;
  first_limit_time?: string;
  last_limit_time?: string;
  seal_amount?: number;
  open_board_count?: number;
  days?: number;
  focus_score?: number;
  sector_rank?: number;
  next_day_plan?: string;
  focus_reasons?: string[];
  openclaw_tier?: "core" | "watch" | "avoid" | "rule" | "unavailable" | string;
  openclaw_score?: number;
  openclaw_action?: string;
  openclaw_risk_level?: string;
  openclaw_summary?: string;
  openclaw_reasons?: string[];
  openclaw_risks?: string[];
  openclaw_review?: Record<string, unknown>;
}

export interface LimitUpAuctionStock {
  code: string;
  name: string;
  price: number;
  open: number;
  high?: number;
  low?: number;
  prev_close: number;
  open_pct: number;
  close_from_open_pct?: number;
  high_from_open_pct?: number;
  low_from_open_pct?: number;
  change_pct: number;
  amount: number;
  volume: number;
  score: number;
  prev_first_board: boolean;
  watching: boolean;
  reasons: string[];
}

export interface LimitUpSector {
  sector: string;
  limit_count: number;
  early_count: number;
  max_streak: number;
  seal_amount: number;
  score: number;
  leader?: LimitUpStock;
  stocks: LimitUpStock[];
}

export interface LimitUpSignal {
  code: string;
  name: string;
  sector: string;
  action: string;
  score: number;
  price: number;
  open_pct: number;
  amount: number;
  reasons: string[];
  risk_note: string;
}

export interface LimitUpPayload {
  date: string;
  previous_date: string;
  source: string;
  ts: number;
  session?: RuntimeStatus["session"];
  summary: {
    zt_count: number;
    first_board: number;
    second_board: number;
    height: number;
    strong_sector_count: number;
    strong_auction_count: number;
    signal_count: number;
  };
  auction: LimitUpAuctionStock[];
  sectors: LimitUpSector[];
  ladders: {
    first_board: LimitUpStock[];
    second_board: LimitUpStock[];
    high_board: LimitUpStock[];
  };
  signals: LimitUpSignal[];
  errors?: string[];
}

export interface LimitUpTomorrowFocusPayload {
  date: string;
  next_date: string;
  source: string;
  ts: number;
  summary: {
    zt_count: number;
    focus_count: number;
    review_buy_count?: number;
    watch_count: number;
    strong_sector_count: number;
    height: number;
  };
  focus: LimitUpStock[];
  watch_pool: LimitUpStock[];
  sectors: LimitUpSector[];
  openclaw_review?: {
    ts: number;
    date: string;
    max_items: number;
    reviewed_count: number;
    mode?: string;
    available?: boolean;
    summary?: string;
    market_view?: string;
    elapsed_ms?: number;
    core_count: number;
    watch_count: number;
    avoid_count: number;
    unavailable_count: number;
  };
  errors?: string[];
}

export interface LimitUpOpenClawJob {
  id: string;
  status: "queued" | "running" | "done" | "fallback" | "failed" | "missing" | string;
  date?: string;
  max_items?: number;
  timeout?: number;
  notify?: boolean;
  created_at?: number;
  started_at?: number;
  finished_at?: number;
  elapsed_ms?: number;
  summary?: string;
  error?: string;
}

export interface LimitUpOpenClawJobPayload {
  job: LimitUpOpenClawJob;
  payload?: LimitUpTomorrowFocusPayload;
}

export interface LimitUpNextDayRow {
  code: string;
  name: string;
  sector: string;
  source_streak: number;
  source_first_limit_time?: string;
  price: number;
  open: number;
  high?: number;
  low?: number;
  prev_close: number;
  open_pct: number;
  close_from_open_pct?: number;
  high_from_open_pct?: number;
  low_from_open_pct?: number;
  change_pct: number;
  amount: number;
  sealed_today: boolean;
  today_first_limit_time?: string;
  today_last_limit_time?: string;
  today_open_board_count?: number;
  buy_stage?: string;
  buy_unavailable?: boolean;
  signal_stage?: "trial" | "official" | "watch" | string;
  tradability?: "tradable" | "queue" | "unavailable" | string;
  trade_hint?: string;
  official_buy?: boolean;
  official_rank?: number;
  official_locked_at?: number;
  official_trigger_time?: string;
  official_trigger_price?: number;
  official_entry_price?: number;
  official_reason?: string;
  execution_status?: "triggered" | "filled" | "missed" | "abandoned" | string;
  execution_price?: number;
  execution_shares?: number;
  execution_note?: string;
  openclaw_tier?: "core" | "watch" | "avoid" | "rule" | "unavailable" | string;
  openclaw_score?: number;
  openclaw_summary?: string;
  openclaw_intraday_status?: "normal" | "downgraded" | string;
  action: "BUY" | "WATCH" | "PASS" | string;
  state: string;
  score: number;
  reasons: string[];
  kline_signal?: "strong" | "watch" | "weak" | "unavailable" | string;
  kline_source?: string;
  kline_reasons?: string[];
  kline_risks?: string[];
  kline_last_time?: string;
  kline_rise_3m_pct?: number;
  kline_vwap?: number;
  kline_dimensions?: Record<string, number>;
  sector_trend?: "enhancing" | "normal" | "fading" | string;
  risk_note: string;
}

export interface LimitUpNextDayPayload {
  date: string;
  source_date: string;
  source: string;
  ts: number;
  phase?: {
    code?: string;
    label?: string;
    remaining_slots?: number | null;
  };
  focus: LimitUpStock[];
  watch_pool: LimitUpStock[];
  today_pool?: LimitUpStock[];
  rows: LimitUpNextDayRow[];
  buy_signals: LimitUpNextDayRow[];
  today_sectors: LimitUpSector[];
  data_quality?: {
    quote_count: number;
    watch_count: number;
    kline_requested_count: number;
    kline_ready_count: number;
    kline_source_counts: Record<string, number>;
    today_pool_count: number;
    today_pool_ignored: boolean;
    updated_at: number;
    source: string;
  };
  runtime?: {
    status?: string;
    source?: string;
    data_age_sec?: number | null;
    error_count?: number;
    retry_count?: number;
    limit_up_stream?: {
      status: string;
      last_publish_ts: number;
      last_tick_ts: number;
      publish_age_sec?: number | null;
      tick_age_sec?: number | null;
      publish_count: number;
      client_count: number;
      drop_count: number;
      interval_sec: number;
    };
  };
  notification_reliability?: {
    sample_count: number;
    success_count: number;
    failure_count: number;
    success_rate: number;
    avg_elapsed_ms: number;
    pending_retry_count: number;
    last_error?: string;
    recent?: Array<Record<string, unknown>>;
  };
  permission?: {
    status: "normal" | "reduced" | "blocked" | string;
    label: string;
    level: string;
    reason: string;
    loss_streak: number;
    max_drawdown_pct: number;
    failed_today: number;
    remaining_slots: number | null;
    equity: number;
  };
  summary: {
    watch_count: number;
    today_limit_count?: number;
    active_count: number;
    buy_signal_count: number;
    review_buy_count?: number;
    remaining_buy_slots?: number | null;
    opportunity_count?: number;
    sealed_count: number;
  };
  opportunity_signals?: LimitUpNextDayRow[];
  errors?: string[];
}

export interface LimitUpSystemReviewRow {
  code: string;
  name: string;
  sector: string;
  rank: number;
  trade_date?: string;
  opened_at?: string;
  trade_action?: "buy" | "hold" | "sell" | string;
  planned_action?: string;
  actual_action?: string;
  execution_status?: string;
  t1_status?: string;
  entry_price: number;
  price: number;
  allocated_capital: number;
  shares: number;
  invested_amount: number;
  fee?: number;
  market_value?: number;
  exit?: boolean;
  exit_amount?: number;
  pnl_amount: number;
  pnl_pct: number;
  change_pct: number;
  from_open_pct: number;
  sealed_today: boolean;
  state: string;
  action: string;
  position_status: string;
  failure_reason?: string;
}

export interface LimitUpSystemReviewRecord {
  date: string;
  source_date?: string;
  ts?: number;
  capital: number;
  start_equity?: number;
  position_count: number;
  invested_amount: number;
  cash: number;
  market_value?: number;
  equity?: number;
  pnl_amount: number;
  pnl_pct: number;
  total_return_pct?: number;
  best_pnl_pct: number;
  worst_pnl_pct: number;
  seal_count: number;
  seal_rate: number;
  hold_count: number;
  rebalance_count: number;
  clear_count: number;
  buy_count?: number;
  sell_count?: number;
  drawdown_pct?: number;
  decision?: {
    action: string;
    level: string;
    reason: string;
  };
  ending_positions?: LimitUpSystemReviewRow[];
  trades?: LimitUpSystemTrade[];
  rows: LimitUpSystemReviewRow[];
}

export interface LimitUpSystemTrade {
  date: string;
  side: "buy" | "sell" | string;
  code: string;
  name: string;
  price: number;
  shares: number;
  amount: number;
  fee: number;
  reason: string;
  execution_status?: string;
}

export interface LimitUpSystemReviewPayload {
  date?: string;
  capital: number;
  max_positions?: number | null;
  selected?: LimitUpSystemReviewRecord | null;
  history: LimitUpSystemReviewRecord[];
  positions?: LimitUpSystemReviewRow[];
  trades?: LimitUpSystemTrade[];
  rules?: Array<{
    title: string;
    badge: string;
    level: string;
    detail: string;
  }>;
  dates: string[];
  stats: {
    trade_days: number;
    equity: number;
    total_pnl: number;
    total_return_pct: number;
    win_rate: number;
    max_drawdown_pct: number;
    loss_streak: number;
  };
  failure_attribution: Array<{
    reason: string;
    count: number;
  }>;
}

export interface PreferencePayload {
  preferences: {
    watchlist: string[];
    blocklist: string[];
  };
}

export type TradeMark = "bought" | "wait_pullback" | "passed";

export interface TradeMarksPayload {
  marks: Record<string, {
    mark: TradeMark;
    updated_ts?: number;
  }>;
}

export interface Position {
  code: string;
  name: string;
  sector: string;
  buy_price: number;
  shares: number;
  source?: "limit-up" | "trend" | "manual" | string;
  buy_date?: string;
  updated_ts?: number;
}

export interface PositionsPayload {
  positions: Position[];
}

export interface TradeRecord {
  id: string;
  ts: number;
  code: string;
  name: string;
  sector: string;
  side: string;
  price: number;
  shares: number;
  amount: number;
  reason?: string;
  source?: string;
}

export interface TradeRecordsPayload {
  records: TradeRecord[];
}

export interface StockOption {
  code: string;
  symbol: string;
  name: string;
  sector?: string;
}

export interface StockSearchPayload {
  stocks: StockOption[];
}

export interface StockLookupPayload {
  stocks: Record<string, StockOption>;
}

export interface FocusReviewRecord {
  key: string;
  trigger_date: string;
  code: string;
  name: string;
  sector: string;
  score: number;
  adjusted_score?: number;
  trigger_price: number;
  trigger_change_pct: number;
  rise_speed_pct: number;
  min2_amount: number;
  active_buy_ratio: number;
  quality_label?: string;
  explanation?: string;
  leader_role?: string;
  leader_score?: number;
  market_height_rank?: number;
  theme_leader_rank?: number;
  limit_up?: boolean;
  limit_up_streak?: number;
  first_limit_time?: string;
  last_limit_time?: string;
  distance_to_limit_pct?: number;
  status: string;
  next_day_date?: string;
  next_return_pct?: number;
  next_high_return_pct?: number;
  next_low_return_pct?: number;
  review_score?: number;
  review_label?: string;
  review_note?: string;
  intraday_score?: number;
  intraday_label?: string;
  intraday_note?: string;
  intraday_current_return_pct?: number;
  intraday_max_return_pct?: number;
  intraday_min_return_pct?: number;
  strategy_version?: string;
  shadow?: boolean;
}

export interface FocusAdvicePayload {
  sample_count: number;
  shadow_sample_count: number;
  stats: FocusReviewStats;
  shadow_stats: FocusReviewStats;
  advices: Array<{
    kind: string;
    title: string;
    problem: string;
    evidence: string;
    action: string;
  }>;
}

export interface FocusReviewStats {
  total: number;
  intraday_count: number;
  next_day_count: number;
  intraday_continue_rate: number;
  intraday_pullback_rate: number;
  intraday_weak_rate: number;
  next_positive_rate: number;
  next_strong_rate: number;
  next_weak_rate: number;
  avg_intraday_score: number;
  avg_intraday_return_pct: number;
  avg_intraday_high_pct: number;
  avg_intraday_low_pct: number;
  avg_next_return_pct: number;
  avg_next_high_pct: number;
  avg_review_score: number;
  intraday_labels: Record<string, number>;
  review_labels: Record<string, number>;
  top_sectors: Array<{
    sector: string;
    sample_count: number;
    intraday_count: number;
    next_day_count: number;
    avg_intraday_score: number;
    avg_review_score: number;
    avg_next_return_pct: number;
    avg_intraday_high_pct: number;
  }>;
}

export interface FocusStrategyPayload {
  days: Array<{
    date: string;
    strategy_version: string;
    score: number;
    sample_count: number;
    tracked_count: number;
    positive_rate: number;
    avg_return_pct: number;
    avg_high_return_pct: number;
    avg_low_return_pct: number;
    best_sector: string;
    suggestion: string;
  }>;
  versions: Array<{
    strategy_version: string;
    day_count: number;
    tracked_day_count: number;
    avg_score: number;
    avg_positive_rate: number;
    avg_return_pct: number;
  }>;
  overall: {
    day_count: number;
    tracked_day_count: number;
    avg_score: number;
    avg_positive_rate: number;
    avg_return_pct: number;
    avg_high_return_pct: number;
  };
}
