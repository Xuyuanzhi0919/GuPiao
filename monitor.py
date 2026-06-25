from __future__ import annotations

from collections import defaultdict, deque
from collections import Counter
from dataclasses import asdict, dataclass
import os
from typing import Iterable

from config import MARKET_RULES, MONITOR_CONFIG
from market_data import Tick

SIGNAL_STALE_SEC = int(os.environ.get("SIGNAL_STALE_SEC", "45"))


@dataclass
class Signal:
    code: str
    name: str
    sector: str
    board: str
    price: float
    change_pct: float
    rise_1m_pct: float
    rise_3m_pct: float
    rise_5m_pct: float
    turnover_1m: float
    turnover_today: float
    volume_spike: float
    active_buy_ratio: float
    order_book_bias: float
    sector_heat: int
    distance_to_limit_pct: float
    score: int
    grade: str
    reasons: list[str]
    quality_tags: list[str]
    ts: float

    def to_dict(self) -> dict:
        return asdict(self)


class IntradayMonitor:
    def __init__(self) -> None:
        self._history: dict[str, deque[Tick]] = defaultdict(lambda: deque(maxlen=360))
        self._turnover_today: dict[str, float] = defaultdict(float)
        self._recent_sector_hits: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=80))
        self._last_signals: dict[str, Signal] = {}
        self._last_alerts: dict[str, Signal] = {}
        self._tracked_alerts: deque[dict] = deque(maxlen=120)

    def update(self, ticks: Iterable[Tick]) -> list[Signal]:
        signals: list[Signal] = []
        batch_ticks = list(ticks)
        batch_ts = max((tick.ts for tick in batch_ticks), default=0.0)
        for tick in batch_ticks:
            history = self._history[tick.code]
            history.append(tick)
            self._turnover_today[tick.code] += tick.turnover
            self._update_tracks(tick)

            if len(history) < 8:
                continue

            signal = self._evaluate(tick, history)
            if signal:
                self._recent_sector_hits[tick.sector].append(tick.ts)
                self._last_signals[tick.code] = signal
                if self._should_alert(signal):
                    signals.append(signal)
                    self._last_alerts[tick.code] = signal
                    self._start_track(signal)
            else:
                self._last_signals.pop(tick.code, None)

        self._prune_stale_signals(batch_ts)
        return sorted(signals, key=lambda item: item.score, reverse=True)

    def snapshot(self) -> dict:
        signals = sorted(self._last_signals.values(), key=lambda item: item.score, reverse=True)
        return {
            "signals": [self._signal_payload(signal) for signal in signals[:50]],
            "tracked_alerts": self._tracked_payload(),
            "performance": self._performance_summary(),
            "sector_heat": self._sector_heat(),
            "config": MONITOR_CONFIG,
        }

    def _signal_payload(self, signal: Signal) -> dict:
        payload = signal.to_dict()
        history = self._history.get(signal.code, ())
        payload["sparkline"] = [tick.price for tick in list(history)[-90:]]
        if history:
            payload["signal_age_sec"] = max(0, round(history[-1].ts - signal.ts, 1))
        return payload

    def _prune_stale_signals(self, now: float) -> None:
        if not now:
            return
        stale_codes = [
            code
            for code, signal in self._last_signals.items()
            if now - signal.ts > SIGNAL_STALE_SEC
        ]
        for code in stale_codes:
            self._last_signals.pop(code, None)

    def _start_track(self, signal: Signal) -> None:
        self._tracked_alerts.appendleft(
            {
                "code": signal.code,
                "name": signal.name,
                "sector": signal.sector,
                "grade": signal.grade,
                "score": signal.score,
                "trigger_price": signal.price,
                "trigger_ts": signal.ts,
                "current_price": signal.price,
                "current_return_pct": 0.0,
                "max_return_pct": 0.0,
                "min_return_pct": 0.0,
                "age_sec": 0,
            }
        )

    def _update_tracks(self, tick: Tick) -> None:
        for track in self._tracked_alerts:
            if track["code"] != tick.code:
                continue
            current_return = (tick.price / track["trigger_price"] - 1) * 100
            track["current_price"] = tick.price
            track["current_return_pct"] = round(current_return, 2)
            track["max_return_pct"] = round(max(track["max_return_pct"], current_return), 2)
            track["min_return_pct"] = round(min(track["min_return_pct"], current_return), 2)
            track["age_sec"] = int(tick.ts - track["trigger_ts"])

    def _tracked_payload(self) -> list[dict]:
        fresh_tracks = [track for track in self._tracked_alerts if track["age_sec"] <= 1800]
        return fresh_tracks[:40]

    def tracked_export_rows(self) -> list[dict]:
        return [dict(track) for track in self._tracked_alerts]

    def report(self) -> dict:
        signals = sorted(self._last_signals.values(), key=lambda item: item.score, reverse=True)
        sector_counts = Counter(signal.sector for signal in signals)
        grade_counts = Counter(signal.grade for signal in signals)
        tag_counts = Counter(tag for signal in signals for tag in signal.quality_tags)
        return {
            "total_signals": len(signals),
            "sector_counts": dict(sector_counts.most_common(10)),
            "grade_counts": dict(grade_counts),
            "tag_counts": dict(tag_counts.most_common(10)),
            "performance": self._performance_summary(),
            "top_signals": [signal.to_dict() for signal in signals[:10]],
            "tracked_alerts": self._tracked_payload()[:10],
        }

    def _performance_summary(self) -> dict:
        tracks = [track for track in self._tracked_alerts if track["age_sec"] >= 10]
        if not tracks:
            return {"total": 0, "positive_rate": 0.0, "by_grade": {}}

        by_grade: dict[str, list[dict]] = defaultdict(list)
        for track in tracks:
            by_grade[track["grade"]].append(track)

        return {
            "total": len(tracks),
            "positive_rate": self._positive_rate(tracks),
            "by_grade": {
                grade: {
                    "count": len(items),
                    "positive_rate": self._positive_rate(items),
                    "avg_current_return_pct": self._avg(items, "current_return_pct"),
                    "avg_max_return_pct": self._avg(items, "max_return_pct"),
                    "avg_min_return_pct": self._avg(items, "min_return_pct"),
                }
                for grade, items in sorted(by_grade.items(), reverse=True)
            },
        }

    def _positive_rate(self, tracks: list[dict]) -> float:
        if not tracks:
            return 0.0
        positive = sum(1 for track in tracks if track["current_return_pct"] > 0)
        return round(positive / len(tracks) * 100, 1)

    def _avg(self, tracks: list[dict], field: str) -> float:
        if not tracks:
            return 0.0
        return round(sum(track[field] for track in tracks) / len(tracks), 2)

    def _evaluate(self, tick: Tick, history: deque[Tick]) -> Signal | None:
        cfg = MONITOR_CONFIG
        if tick.price < cfg["min_price"]:
            return None

        rise_1m = self._rise_since(history, 60)
        rise_3m = self._rise_since(history, 180)
        rise_5m = self._rise_since(history, 300)
        turnover_1m = self._turnover_since(history, 60)
        avg_turnover = max(self._turnover_since(history, 300) / max(1, min(len(history), 300)), 1)
        recent_avg_turnover = max(turnover_1m / max(1, min(len(history), 60)), 1)
        volume_spike = recent_avg_turnover / avg_turnover
        order_book_bias = (tick.bid_amount - tick.ask_amount) / max(tick.bid_amount + tick.ask_amount, 1)
        change_pct = (tick.price / tick.prev_close - 1) * 100
        distance_to_limit = self._distance_to_limit(tick)

        reasons: list[str] = []
        quality_tags: list[str] = []
        score = 0

        if rise_1m >= cfg["rise_1m_pct"]:
            score += 15
            reasons.append("1分钟涨速达标")
        if rise_3m >= cfg["rise_3m_pct"]:
            score += 18
            reasons.append("3分钟快速拉升")
        if rise_5m >= cfg["rise_5m_pct"]:
            score += 12
            reasons.append("5分钟趋势延续")

        if turnover_1m >= cfg["min_turnover_1m"]:
            score += 15
            reasons.append("1分钟成交额放大")
        if self._turnover_today[tick.code] >= cfg["min_turnover_today"]:
            score += 8
            reasons.append("全天流动性足够")
        if volume_spike >= cfg["volume_spike_ratio"]:
            score += 15
            reasons.append("量能突增")
        if tick.active_buy_ratio >= cfg["min_active_buy_ratio"]:
            score += 12
            reasons.append("主动买入占优")
        if order_book_bias >= cfg["min_order_book_bias"]:
            score += 10
            reasons.append("买盘承接强")

        sector_heat = self._sector_heat_for(tick.sector, tick.ts)
        if sector_heat >= 2:
            score += min(10, sector_heat * 3)
            reasons.append("板块共振")

        if distance_to_limit <= cfg["max_distance_to_limit_pct"]:
            score -= 12
            reasons.append("临近涨停，追价风险高")
            quality_tags.append("临近涨停")

        if turnover_1m < cfg["min_turnover_1m"] and volume_spike < cfg["volume_spike_ratio"]:
            return None
        if score < cfg["min_score"]:
            return None

        quality_tags.extend(self._quality_tags(history, rise_1m, rise_3m, tick.active_buy_ratio, order_book_bias))
        grade = "A" if score >= 82 else "B" if score >= 68 else "C"
        return Signal(
            code=tick.code,
            name=tick.name,
            sector=tick.sector,
            board=tick.board,
            price=tick.price,
            change_pct=round(change_pct, 2),
            rise_1m_pct=round(rise_1m, 2),
            rise_3m_pct=round(rise_3m, 2),
            rise_5m_pct=round(rise_5m, 2),
            turnover_1m=round(turnover_1m, 2),
            turnover_today=round(self._turnover_today[tick.code], 2),
            volume_spike=round(volume_spike, 2),
            active_buy_ratio=tick.active_buy_ratio,
            order_book_bias=round(order_book_bias, 2),
            sector_heat=sector_heat,
            distance_to_limit_pct=round(distance_to_limit, 2),
            score=max(0, min(100, score)),
            grade=grade,
            reasons=reasons,
            quality_tags=quality_tags,
            ts=tick.ts,
        )

    def _should_alert(self, signal: Signal) -> bool:
        previous = self._last_alerts.get(signal.code)
        if previous is None:
            return True

        cfg = MONITOR_CONFIG
        grade_rank = {"C": 1, "B": 2, "A": 3}
        grade_improved = grade_rank[signal.grade] > grade_rank[previous.grade]
        score_jump = signal.score - previous.score >= cfg["signal_rescore_delta"]
        cooldown_expired = signal.ts - previous.ts >= cfg["signal_cooldown_sec"]

        return grade_improved or score_jump or cooldown_expired

    def _rise_since(self, history: deque[Tick], seconds: int) -> float:
        current = history[-1]
        baseline = self._first_after(history, current.ts - seconds)
        return (current.price / baseline.price - 1) * 100

    def _turnover_since(self, history: deque[Tick], seconds: int) -> float:
        cutoff = history[-1].ts - seconds
        return sum(item.turnover for item in history if item.ts >= cutoff)

    def _quality_tags(
        self,
        history: deque[Tick],
        rise_1m: float,
        rise_3m: float,
        active_buy_ratio: float,
        order_book_bias: float,
    ) -> list[str]:
        prices = [tick.price for tick in list(history)[-90:]]
        if len(prices) < 8:
            return []

        tags: list[str] = []
        current = prices[-1]
        high = max(prices)
        low = min(prices)
        pullback_from_high = (high / current - 1) * 100
        range_pct = (high / max(low, 0.01) - 1) * 100
        positive_steps = sum(1 for left, right in zip(prices, prices[1:]) if right >= left)
        trend_ratio = positive_steps / max(1, len(prices) - 1)

        if rise_3m >= 3 and trend_ratio >= 0.68:
            tags.append("直线拉升")
        elif rise_3m >= 1.5 and trend_ratio >= 0.55:
            tags.append("震荡推升")

        if pullback_from_high >= 1.2 and rise_1m < 0.2:
            tags.append("冲高回落")
        elif pullback_from_high <= 0.35 and rise_3m >= 1.2:
            tags.append("高位维持")

        if active_buy_ratio >= 0.62 and order_book_bias >= 0.12:
            tags.append("买盘强")
        if range_pct >= 4 and active_buy_ratio < 0.56:
            tags.append("分歧放大")

        return tags[:4]

    def _first_after(self, history: deque[Tick], cutoff: float) -> Tick:
        for item in history:
            if item.ts >= cutoff:
                return item
        return history[0]

    def _distance_to_limit(self, tick: Tick) -> float:
        limit_pct = MARKET_RULES.get(tick.board, MARKET_RULES["main"])["limit_pct"]
        limit_price = tick.prev_close * (1 + limit_pct / 100)
        return max(0.0, (limit_price / tick.price - 1) * 100)

    def _sector_heat_for(self, sector: str, now: float) -> int:
        window = MONITOR_CONFIG["sector_signal_window_sec"]
        hits = self._recent_sector_hits[sector]
        while hits and hits[0] < now - window:
            hits.popleft()
        return len(hits)

    def _sector_heat(self) -> dict[str, int]:
        now = max((history[-1].ts for history in self._history.values() if history), default=0)
        return {sector: self._sector_heat_for(sector, now) for sector in self._recent_sector_hits}
