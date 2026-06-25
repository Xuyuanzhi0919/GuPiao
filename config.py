MARKET_RULES = {
    "main": {"limit_pct": 10.0},
    "st": {"limit_pct": 5.0},
    "star": {"limit_pct": 20.0},
    "gem": {"limit_pct": 20.0},
    "bj": {"limit_pct": 30.0},
}

MONITOR_CONFIG = {
    "min_price": 2.0,
    "min_turnover_1m": 8_000_000,
    "min_turnover_today": 30_000_000,
    "min_score": 55,
    "max_distance_to_limit_pct": 1.0,
    "rise_1m_pct": 0.65,
    "rise_3m_pct": 1.35,
    "rise_5m_pct": 2.0,
    "volume_spike_ratio": 2.2,
    "min_active_buy_ratio": 0.54,
    "min_order_book_bias": 0.08,
    "sector_signal_window_sec": 90,
    "signal_cooldown_sec": 75,
    "signal_rescore_delta": 12,
}
