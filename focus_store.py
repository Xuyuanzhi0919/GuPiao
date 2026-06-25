from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from market_data import Tick
from market_clock import is_trading_date, next_trading_date
from candidate_quality import STRATEGIES, candidate_quality

CN_TZ = ZoneInfo("Asia/Shanghai")


def trading_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, CN_TZ).strftime("%Y-%m-%d")


def valid_trigger_day(value: str) -> bool:
    try:
        return is_trading_date(value)
    except (TypeError, ValueError):
        return False


class FocusStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: dict[str, dict] = self._load()

    def record_candidates(self, payload: dict) -> None:
        candidates = payload.get("candidates", [])
        sector_heat = payload.get("sector_heat") or payload.get("health", {}).get("sector_heat") or []
        sector_counts = {item.get("sector"): int(item.get("count", 0)) for item in sector_heat if isinstance(item, dict)}
        changed = False
        for item in candidates:
            if not isinstance(item, dict):
                continue
            quality = candidate_quality(item, sector_counts)
            item.update(quality)
            versions = [quality, *(item.get("shadow_strategies") or [])]
            for version_quality in versions:
                if version_quality.get("quality_level") != "strong":
                    continue
                if self._record_focus_candidate(item, version_quality):
                    changed = True
        if changed:
            self._save()

    def _record_focus_candidate(self, item: dict, quality: dict) -> bool:
        ts = float(item.get("ts") or item.get("timestamp") or datetime.now(CN_TZ).timestamp())
        code = str(item.get("code", ""))
        if not code:
            return False
        date = trading_day(ts)
        if not valid_trigger_day(date):
            return False
        version = quality.get("strategy_version") or "focus-v1"
        key = f"{date}:{version}:{code}"
        score = round(float(item.get("candidate_score", 0)), 2)
        existing = self.records.get(key)
        if existing and score <= float(existing.get("score", 0)):
            for field in ("strategy_version", "quality_rule", "quality_level", "quality_label", "adjusted_score", "explanation", "shadow"):
                existing[field] = quality.get(field, existing.get(field, ""))
            return True
        self.records[key] = {
            "key": key,
            "trigger_date": date,
            "trigger_ts": existing.get("trigger_ts", ts) if existing else ts,
            "code": code,
            "name": item.get("name", code),
            "sector": item.get("sector", "未分组"),
            "board": item.get("board", ""),
            "trigger_price": existing.get("trigger_price", round(float(item.get("price", 0)), 3)) if existing else round(float(item.get("price", 0)), 3),
            "trigger_change_pct": existing.get("trigger_change_pct", round(float(item.get("change_pct", 0)), 2)) if existing else round(float(item.get("change_pct", 0)), 2),
            "score": score,
            "rise_speed_pct": round(float(item.get("rise_speed_pct", 0)), 2),
            "min2_amount": round(float(item.get("min2_amount", 0)), 2),
            "active_buy_ratio": round(float(item.get("active_buy_ratio", 0)), 4),
            "quality_level": quality.get("quality_level", ""),
            "quality_label": quality.get("quality_label", ""),
            "strategy_version": version,
            "quality_rule": quality.get("quality_rule", {}),
            "adjusted_score": quality.get("adjusted_score", 0),
            "explanation": quality.get("explanation", ""),
            "market_mood": item.get("market_mood", ""),
            "emotion_score": item.get("emotion_score", 0),
            "theme_rank": item.get("theme_rank", 0),
            "theme_score": item.get("theme_score", 0),
            "hot_money_role": item.get("hot_money_role", ""),
            "leader_role": item.get("leader_role", ""),
            "leader_score": item.get("leader_score", 0),
            "market_height_rank": item.get("market_height_rank", 0),
            "theme_leader_rank": item.get("theme_leader_rank", 0),
            "limit_up": bool(item.get("limit_up")),
            "limit_up_streak": item.get("limit_up_streak", 0),
            "first_limit_time": item.get("first_limit_time", ""),
            "last_limit_time": item.get("last_limit_time", ""),
            "seal_amount": item.get("seal_amount", 0),
            "open_board_count": item.get("open_board_count", 0),
            "distance_to_limit_pct": item.get("distance_to_limit_pct", 0),
            "buy_pattern": item.get("buy_pattern", ""),
            "hot_money_tags": item.get("hot_money_tags", []),
            "shadow": bool(quality.get("shadow")),
            "status": existing.get("status", "等待次日") if existing else "等待次日",
            "next_day_date": existing.get("next_day_date", "") if existing else "",
            "expected_next_trading_date": existing.get("expected_next_trading_date", next_trading_date(date)) if existing else next_trading_date(date),
            "next_open_price": existing.get("next_open_price", 0) if existing else 0,
            "next_prev_close": existing.get("next_prev_close", 0) if existing else 0,
            "next_current_price": existing.get("next_current_price", 0) if existing else 0,
            "next_high_price": existing.get("next_high_price", 0) if existing else 0,
            "next_low_price": existing.get("next_low_price", 0) if existing else 0,
            "gap_pct": existing.get("gap_pct", 0) if existing else 0,
            "next_return_pct": existing.get("next_return_pct", 0) if existing else 0,
            "next_high_return_pct": existing.get("next_high_return_pct", 0) if existing else 0,
            "next_low_return_pct": existing.get("next_low_return_pct", 0) if existing else 0,
            "next_drawdown_pct": existing.get("next_drawdown_pct", 0) if existing else 0,
            "next_giveback_pct": existing.get("next_giveback_pct", 0) if existing else 0,
            "review_score": existing.get("review_score", 0) if existing else 0,
            "review_label": existing.get("review_label", "") if existing else "",
            "review_note": existing.get("review_note", "") if existing else "",
            "intraday_age_sec": existing.get("intraday_age_sec", 0) if existing else 0,
            "intraday_current_return_pct": existing.get("intraday_current_return_pct", 0) if existing else 0,
            "intraday_max_return_pct": existing.get("intraday_max_return_pct", 0) if existing else 0,
            "intraday_min_return_pct": existing.get("intraday_min_return_pct", 0) if existing else 0,
            "intraday_m1_return_pct": existing.get("intraday_m1_return_pct", "") if existing else "",
            "intraday_m3_return_pct": existing.get("intraday_m3_return_pct", "") if existing else "",
            "intraday_m5_return_pct": existing.get("intraday_m5_return_pct", "") if existing else "",
            "intraday_m10_return_pct": existing.get("intraday_m10_return_pct", "") if existing else "",
            "intraday_score": existing.get("intraday_score", 0) if existing else 0,
            "intraday_label": existing.get("intraday_label", "") if existing else "",
            "intraday_note": existing.get("intraday_note", "") if existing else "",
            "updated_ts": existing.get("updated_ts", ts) if existing else ts,
        }
        return True

    def update_ticks(self, ticks: Iterable[Tick]) -> None:
        changed = False
        by_code = {tick.code: tick for tick in ticks}
        for record in self.records.values():
            if not valid_trigger_day(str(record.get("trigger_date", ""))):
                continue
            tick = by_code.get(record["code"])
            if not tick:
                continue
            current_day = trading_day(tick.ts)
            if not valid_trigger_day(current_day):
                continue
            if current_day == record.get("trigger_date") and tick.ts >= float(record.get("trigger_ts") or 0):
                if self._update_intraday_track(record, tick):
                    changed = True
            expected_day = record.get("expected_next_trading_date") or next_trading_date(record["trigger_date"])
            record["expected_next_trading_date"] = expected_day
            if current_day < expected_day:
                continue
            if current_day > expected_day and not record.get("next_day_date"):
                record["status"] = "错过次日"
                record["updated_ts"] = tick.ts
                changed = True
                continue
            if not record.get("next_day_date"):
                record["next_day_date"] = current_day
                record["next_open_price"] = round(tick.price, 3)
                record["next_prev_close"] = round(tick.prev_close, 3)
                record["next_high_price"] = round(tick.price, 3)
                record["next_low_price"] = round(tick.price, 3)
            if current_day != record["next_day_date"]:
                continue
            prev_close = max(float(record.get("next_prev_close") or tick.prev_close), 0.01)
            high = max(float(record.get("next_high_price") or tick.price), tick.price)
            low = min(float(record.get("next_low_price") or tick.price), tick.price)
            record["status"] = "次日跟踪中"
            record["next_current_price"] = round(tick.price, 3)
            record["next_high_price"] = round(high, 3)
            record["next_low_price"] = round(low, 3)
            record["gap_pct"] = self._pct(float(record.get("next_open_price") or tick.price), prev_close)
            record["next_return_pct"] = self._pct(tick.price, prev_close)
            record["next_high_return_pct"] = self._pct(high, prev_close)
            record["next_low_return_pct"] = self._pct(low, prev_close)
            record["next_drawdown_pct"] = round(record["next_high_return_pct"] - record["next_low_return_pct"], 2)
            record["next_giveback_pct"] = round(record["next_high_return_pct"] - record["next_return_pct"], 2)
            review = self._review_result(record)
            record.update(review)
            record["updated_ts"] = tick.ts
            changed = True
        if changed:
            self._save()

    def latest(self, limit: int = 100, include_shadow: bool = False) -> list[dict]:
        records = self.records.values() if include_shadow else [item for item in self.records.values() if not item.get("shadow")]
        records = [item for item in records if valid_trigger_day(str(item.get("trigger_date", "")))]
        rows = sorted(records, key=lambda item: (item.get("trigger_date", ""), item.get("score", 0)), reverse=True)
        return rows[:limit]

    def strategy_summary(self, limit_days: int = 30) -> dict:
        grouped: dict[str, list[dict]] = {}
        for row in self.records.values():
            if not valid_trigger_day(str(row.get("trigger_date", ""))):
                continue
            grouped.setdefault(self._strategy_key(row), []).append(row)
        actual_versions = {key.split("|", 1)[1] for key in grouped}
        for version in STRATEGIES:
            if version not in actual_versions:
                grouped.setdefault(f"|{version}", [])

        days = []
        for key, rows in grouped.items():
            date, version = key.split("|", 1)
            if not rows:
                days.append(
                    {
                        "date": "--",
                        "strategy_version": version,
                        "score": 0,
                        "sample_count": 0,
                        "tracked_count": 0,
                        "positive_rate": 0,
                        "avg_return_pct": 0,
                        "avg_high_return_pct": 0,
                        "avg_low_return_pct": 0,
                        "best_sector": "--",
                        "suggestion": "影子策略运行中，等待命中样本",
                    }
                )
                continue
            tracked = [row for row in rows if row.get("next_day_date")]
            positive = [row for row in tracked if float(row.get("next_return_pct", 0)) > 0]
            avg_return = self._avg(tracked, "next_return_pct")
            avg_high = self._avg(tracked, "next_high_return_pct")
            avg_low = self._avg(tracked, "next_low_return_pct")
            score = self._strategy_score(len(rows), len(tracked), len(positive), avg_return, avg_high, avg_low)
            best_sector = self._best_sector(tracked)
            days.append(
                {
                    "date": date,
                    "strategy_version": version,
                    "score": score,
                    "sample_count": len(rows),
                    "tracked_count": len(tracked),
                    "positive_rate": round((len(positive) / len(tracked)) * 100, 1) if tracked else 0,
                    "avg_return_pct": round(avg_return, 2),
                    "avg_high_return_pct": round(avg_high, 2),
                    "avg_low_return_pct": round(avg_low, 2),
                    "best_sector": best_sector,
                    "suggestion": self._suggestion(len(rows), len(tracked), len(positive), avg_return, avg_high, avg_low),
                }
            )

        days = sorted(days, key=lambda item: (item["date"], item["strategy_version"]), reverse=True)[:limit_days]
        tracked_days = [day for day in days if day["tracked_count"]]
        return {
            "days": days,
            "versions": self._version_summary(days),
            "overall": {
                "day_count": len(days),
                "tracked_day_count": len(tracked_days),
                "avg_score": round(sum(day["score"] for day in tracked_days) / len(tracked_days), 1) if tracked_days else 0,
                "avg_positive_rate": round(sum(day["positive_rate"] for day in tracked_days) / len(tracked_days), 1) if tracked_days else 0,
                "avg_return_pct": round(sum(day["avg_return_pct"] for day in tracked_days) / len(tracked_days), 2) if tracked_days else 0,
                "avg_high_return_pct": round(sum(day["avg_high_return_pct"] for day in tracked_days) / len(tracked_days), 2) if tracked_days else 0,
            },
        }

    def advice_summary(self, limit: int = 300) -> dict:
        records = self.latest(limit=limit, include_shadow=True)
        primary = [row for row in records if not row.get("shadow")]
        shadow = [row for row in records if row.get("shadow")]
        primary_stats = self._advice_stats(primary)
        shadow_stats = self._advice_stats(shadow)
        advices = self._build_advices(primary_stats, shadow_stats)
        return {
            "sample_count": len(primary),
            "shadow_sample_count": len(shadow),
            "stats": primary_stats,
            "shadow_stats": shadow_stats,
            "advices": advices,
        }

    def export_csv(self, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "trigger_date",
            "code",
            "name",
            "sector",
            "score",
            "trigger_price",
            "trigger_change_pct",
            "rise_speed_pct",
            "active_buy_ratio",
            "quality_level",
            "strategy_version",
            "adjusted_score",
            "status",
            "next_day_date",
            "expected_next_trading_date",
            "gap_pct",
            "next_return_pct",
            "next_high_return_pct",
            "next_low_return_pct",
            "next_drawdown_pct",
            "next_giveback_pct",
            "review_score",
            "review_label",
            "review_note",
            "intraday_age_sec",
            "intraday_current_return_pct",
            "intraday_max_return_pct",
            "intraday_min_return_pct",
            "intraday_m1_return_pct",
            "intraday_m3_return_pct",
            "intraday_m5_return_pct",
            "intraday_m10_return_pct",
            "intraday_score",
            "intraday_label",
            "intraday_note",
        ]
        with target.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            for row in self.latest(limit=10000):
                writer.writerow({field: row.get(field, "") for field in fields})
        return target

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        records = {}
        for item in payload.get("records", []):
            trigger_date = str(item.get("trigger_date", ""))
            if not valid_trigger_day(trigger_date):
                continue
            version = item.get("strategy_version") or "focus-v1"
            key = str(item.get("key") or f"{item.get('trigger_date')}:{version}:{item.get('code')}")
            if key.count(":") == 1:
                date, code = key.split(":", 1)
                key = f"{date}:{version}:{code}"
                item["key"] = key
            self._normalize_next_day_result(item)
            if item.get("next_day_date"):
                item.update(self._review_result(item))
            item.setdefault("next_drawdown_pct", 0)
            item.setdefault("next_giveback_pct", 0)
            item.setdefault("review_score", 0)
            item.setdefault("review_label", "")
            item.setdefault("review_note", "")
            item.setdefault("intraday_age_sec", 0)
            item.setdefault("intraday_current_return_pct", 0)
            item.setdefault("intraday_max_return_pct", 0)
            item.setdefault("intraday_min_return_pct", 0)
            item.setdefault("intraday_m1_return_pct", "")
            item.setdefault("intraday_m3_return_pct", "")
            item.setdefault("intraday_m5_return_pct", "")
            item.setdefault("intraday_m10_return_pct", "")
            item.setdefault("intraday_score", 0)
            item.setdefault("intraday_label", "")
            item.setdefault("intraday_note", "")
            records[key] = item
        return records

    def _normalize_next_day_result(self, item: dict) -> None:
        trigger_date = str(item.get("trigger_date", ""))
        expected_day = str(item.get("expected_next_trading_date") or "")
        if not valid_trigger_day(expected_day):
            expected_day = next_trading_date(trigger_date)
        item["expected_next_trading_date"] = expected_day

        next_day = str(item.get("next_day_date") or "")
        if next_day and next_day == expected_day and valid_trigger_day(next_day):
            return
        if next_day:
            self._clear_next_day_result(item)

    def _clear_next_day_result(self, item: dict) -> None:
        item["status"] = "等待次日"
        item["next_day_date"] = ""
        for field in (
            "next_open_price",
            "next_prev_close",
            "next_current_price",
            "next_high_price",
            "next_low_price",
            "gap_pct",
            "next_return_pct",
            "next_high_return_pct",
            "next_low_return_pct",
            "next_drawdown_pct",
            "next_giveback_pct",
            "review_score",
        ):
            item[field] = 0
        item["review_label"] = ""
        item["review_note"] = ""

    def _save(self) -> None:
        payload = {"records": self.latest(limit=10000, include_shadow=True)}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _advice_stats(self, rows: list[dict]) -> dict:
        intraday = [row for row in rows if int(row.get("intraday_age_sec") or 0) > 0]
        next_day = [row for row in rows if row.get("next_day_date")]
        label_counts = Counter(row.get("intraday_label") or "未评估" for row in intraday)
        review_counts = Counter(row.get("review_label") or "未评估" for row in next_day)
        sector_rows: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            sector_rows[row.get("sector") or "未分组"].append(row)

        continued_count = sum(label_counts[label] for label in ("持续走强", "小幅延续"))
        weak_count = sum(label_counts[label] for label in ("触发回撤", "未延续"))
        pullback_count = label_counts["冲高回落"]
        positive_next = sum(1 for row in next_day if float(row.get("next_return_pct") or 0) > 0)
        strong_next = sum(1 for row in next_day if row.get("review_label") == "强兑现")
        weak_next = sum(1 for row in next_day if row.get("review_label") in {"低开走弱", "未兑现"})

        sector_stats = []
        for sector, items in sector_rows.items():
            sector_intraday = [row for row in items if int(row.get("intraday_age_sec") or 0) > 0]
            sector_next = [row for row in items if row.get("next_day_date")]
            if not sector_intraday and not sector_next:
                continue
            sector_stats.append(
                {
                    "sector": sector,
                    "sample_count": len(items),
                    "intraday_count": len(sector_intraday),
                    "next_day_count": len(sector_next),
                    "avg_intraday_score": self._avg(sector_intraday, "intraday_score"),
                    "avg_review_score": self._avg(sector_next, "review_score"),
                    "avg_next_return_pct": self._avg(sector_next, "next_return_pct"),
                    "avg_intraday_high_pct": self._avg(sector_intraday, "intraday_max_return_pct"),
                }
            )

        sector_stats = sorted(
            sector_stats,
            key=lambda item: (
                item["avg_review_score"] + item["avg_intraday_score"],
                item["sample_count"],
            ),
            reverse=True,
        )[:8]

        return {
            "total": len(rows),
            "intraday_count": len(intraday),
            "next_day_count": len(next_day),
            "intraday_continue_rate": self._rate(continued_count, len(intraday)),
            "intraday_pullback_rate": self._rate(pullback_count, len(intraday)),
            "intraday_weak_rate": self._rate(weak_count, len(intraday)),
            "next_positive_rate": self._rate(positive_next, len(next_day)),
            "next_strong_rate": self._rate(strong_next, len(next_day)),
            "next_weak_rate": self._rate(weak_next, len(next_day)),
            "avg_intraday_score": self._avg(intraday, "intraday_score"),
            "avg_intraday_return_pct": self._avg(intraday, "intraday_current_return_pct"),
            "avg_intraday_high_pct": self._avg(intraday, "intraday_max_return_pct"),
            "avg_intraday_low_pct": self._avg(intraday, "intraday_min_return_pct"),
            "avg_next_return_pct": self._avg(next_day, "next_return_pct"),
            "avg_next_high_pct": self._avg(next_day, "next_high_return_pct"),
            "avg_review_score": self._avg(next_day, "review_score"),
            "intraday_labels": dict(label_counts.most_common()),
            "review_labels": dict(review_counts.most_common()),
            "top_sectors": sector_stats,
        }

    def _build_advices(self, stats: dict, shadow_stats: dict) -> list[dict]:
        advices = []
        if stats["intraday_count"] < 5:
            advices.append(
                self._advice(
                    "observe",
                    "继续积累样本",
                    "当前强关注盘中样本偏少，暂不建议直接修改主策略。",
                    f"盘中样本 {stats['intraday_count']} 条，建议至少积累 20 条后再调主参数。",
                    "保持当前规则运行，先观察 1/3/5/10 分钟表现。",
                )
            )
            return advices

        if stats["intraday_weak_rate"] >= 45:
            advices.append(
                self._advice(
                    "tighten",
                    "提高触发质量门槛",
                    "强关注触发后弱延续比例偏高，说明部分拉升缺少后续资金承接。",
                    f"弱延续 {stats['intraday_weak_rate']:.1f}%，平均盘中低点 {stats['avg_intraday_low_pct']:.2f}%。",
                    "优先提高主动买入占比、2分钟成交额或板块热度门槛。",
                )
            )

        if stats["intraday_pullback_rate"] >= 35:
            advices.append(
                self._advice(
                    "take-profit",
                    "增加盘中止盈规则",
                    "冲高回落比例偏高，强关注更像短线脉冲而不是持续趋势。",
                    f"冲高回落 {stats['intraday_pullback_rate']:.1f}%，平均最高 {stats['avg_intraday_high_pct']:.2f}%。",
                    "触发后若快速冲高但 3/5 分钟不延续，建议进入止盈或降级观察。",
                )
            )

        if stats["next_day_count"] >= 5 and stats["next_positive_rate"] < 45:
            advices.append(
                self._advice(
                    "intraday-only",
                    "降低隔夜权重",
                    "次日上涨率偏低，强关注信号暂时更适合盘中交易。",
                    f"次日样本 {stats['next_day_count']} 条，上涨率 {stats['next_positive_rate']:.1f}%，平均次日收益 {stats['avg_next_return_pct']:.2f}%。",
                    "复盘页保留次日跟踪，但实际决策优先看盘中延续和回落。",
                )
            )

        if stats["next_day_count"] >= 5 and stats["next_strong_rate"] >= 35 and stats["avg_review_score"] >= 60:
            advices.append(
                self._advice(
                    "hold",
                    "保留当前强关注规则",
                    "次日强兑现比例较好，当前规则有继续观察价值。",
                    f"强兑现 {stats['next_strong_rate']:.1f}%，平均复盘分 {stats['avg_review_score']:.1f}。",
                    "暂不大幅收紧，重点观察表现最好的板块和时间段。",
                )
            )

        if shadow_stats["intraday_count"] >= 5 and shadow_stats["avg_intraday_score"] >= stats["avg_intraday_score"] + 8:
            advices.append(
                self._advice(
                    "shadow-upgrade",
                    "观察影子策略升级",
                    "影子策略的盘中评分明显高于主策略，可能更能过滤噪音。",
                    f"v2 平均盘中分 {shadow_stats['avg_intraday_score']:.1f}，v1 为 {stats['avg_intraday_score']:.1f}。",
                    "继续收集次日样本；若次日也领先，再考虑把 v2 升为主策略。",
                )
            )

        if not advices:
            advices.append(
                self._advice(
                    "keep",
                    "保持当前参数",
                    "当前样本没有出现明显单一问题，暂不需要大幅调参。",
                    f"延续率 {stats['intraday_continue_rate']:.1f}%，冲高回落 {stats['intraday_pullback_rate']:.1f}%，弱延续 {stats['intraday_weak_rate']:.1f}%。",
                    "继续积累样本，优先看板块集中度和次日兑现情况。",
                )
            )
        return advices[:5]

    def _advice(self, kind: str, title: str, problem: str, evidence: str, action: str) -> dict:
        return {
            "kind": kind,
            "title": title,
            "problem": problem,
            "evidence": evidence,
            "action": action,
        }

    def _rate(self, count: int, total: int) -> float:
        return round((count / total) * 100, 1) if total else 0.0

    def _pct(self, price: float, base: float) -> float:
        return round((price / max(base, 0.01) - 1) * 100, 2)

    def _update_intraday_track(self, record: dict, tick: Tick) -> bool:
        trigger_price = float(record.get("trigger_price") or 0)
        trigger_ts = float(record.get("trigger_ts") or 0)
        if trigger_price <= 0 or trigger_ts <= 0:
            return False

        age = max(0, int(tick.ts - trigger_ts))
        current = self._pct(tick.price, trigger_price)
        previous_age = int(record.get("intraday_age_sec") or 0)
        previous_current = float(record.get("intraday_current_return_pct") or 0)
        if age < previous_age:
            return False

        high = max(float(record.get("intraday_max_return_pct") or 0), current)
        low = min(float(record.get("intraday_min_return_pct") or 0), current)
        record["intraday_age_sec"] = age
        record["intraday_current_return_pct"] = current
        record["intraday_max_return_pct"] = round(high, 2)
        record["intraday_min_return_pct"] = round(low, 2)

        milestones = (
            (60, "intraday_m1_return_pct"),
            (180, "intraday_m3_return_pct"),
            (300, "intraday_m5_return_pct"),
            (600, "intraday_m10_return_pct"),
        )
        for seconds, field in milestones:
            if age >= seconds and record.get(field) in ("", None):
                record[field] = current

        record.update(self._intraday_result(record))
        record["updated_ts"] = tick.ts
        return age != previous_age or current != previous_current

    def _intraday_result(self, row: dict) -> dict:
        current = float(row.get("intraday_current_return_pct") or 0)
        high = float(row.get("intraday_max_return_pct") or 0)
        low = float(row.get("intraday_min_return_pct") or 0)
        m3 = self._optional_float(row.get("intraday_m3_return_pct"))
        m10 = self._optional_float(row.get("intraday_m10_return_pct"))
        giveback = high - current

        score = 45
        score += min(22, max(-18, current * 10))
        score += min(18, max(0, high * 8))
        score -= min(14, max(0, giveback * 7))
        score -= min(12, max(0, abs(min(low, 0)) * 8))
        if m3 is not None and m3 > 0.5:
            score += 6
        if m10 is not None and m10 > 0.8:
            score += 8
        score = max(0, min(100, round(score)))

        if m10 is not None and m10 >= 0.8 and giveback <= 0.8:
            label = "持续走强"
            note = "触发后十分钟仍保持强势"
        elif high >= 1.5 and current < 0.3:
            label = "冲高回落"
            note = "触发后给过冲高，但后续承接不足"
        elif low <= -0.8:
            label = "触发回撤"
            note = "触发后快速回撤，追入风险偏高"
        elif current > 0:
            label = "小幅延续"
            note = "触发后仍为正收益，强度一般"
        else:
            label = "未延续"
            note = "触发后没有形成有效延续"

        return {
            "intraday_score": score,
            "intraday_label": label,
            "intraday_note": note,
        }

    def _optional_float(self, value: object) -> float | None:
        if value in ("", None):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _review_result(self, row: dict) -> dict:
        gap = float(row.get("gap_pct", 0) or 0)
        close_ret = float(row.get("next_return_pct", 0) or 0)
        high_ret = float(row.get("next_high_return_pct", 0) or 0)
        low_ret = float(row.get("next_low_return_pct", 0) or 0)
        drawdown = round(high_ret - low_ret, 2)
        giveback = round(high_ret - close_ret, 2)

        score = 50
        score += min(25, max(-25, close_ret * 6))
        score += min(18, max(0, high_ret * 4))
        score += min(8, max(-10, gap * 2))
        score -= min(12, max(0, giveback * 2))
        score -= min(10, max(0, abs(min(low_ret, 0)) * 3))
        score = max(0, min(100, round(score)))

        if high_ret >= 3 and close_ret >= 1:
            label = "强兑现"
            note = "次日冲高且收盘保持收益"
        elif high_ret >= 2 and close_ret < 0.5:
            label = "冲高回落"
            note = "盘中给过收益，收盘兑现不足"
        elif close_ret > 0:
            label = "小幅兑现"
            note = "收盘为正，但弹性一般"
        elif gap < -1 and low_ret < -2:
            label = "低开走弱"
            note = "隔夜风险偏高"
        else:
            label = "未兑现"
            note = "次日表现未达到强关注预期"

        return {
            "next_drawdown_pct": drawdown,
            "next_giveback_pct": giveback,
            "review_score": score,
            "review_label": label,
            "review_note": note,
        }

    def _strategy_key(self, row: dict) -> str:
        return f"{row.get('trigger_date', '')}|{row.get('strategy_version') or 'focus-v1'}"

    def _version_summary(self, days: list[dict]) -> list[dict]:
        grouped: dict[str, list[dict]] = {}
        for day in days:
            grouped.setdefault(day.get("strategy_version", "focus-v1"), []).append(day)
        versions = []
        for version, items in grouped.items():
            tracked = [item for item in items if item["tracked_count"]]
            versions.append(
                {
                    "strategy_version": version,
                    "day_count": len(items),
                    "tracked_day_count": len(tracked),
                    "avg_score": round(sum(item["score"] for item in tracked) / len(tracked), 1) if tracked else 0,
                    "avg_positive_rate": round(sum(item["positive_rate"] for item in tracked) / len(tracked), 1) if tracked else 0,
                    "avg_return_pct": round(sum(item["avg_return_pct"] for item in tracked) / len(tracked), 2) if tracked else 0,
                }
            )
        return sorted(versions, key=lambda item: item["strategy_version"])

    def _avg(self, rows: list[dict], key: str) -> float:
        if not rows:
            return 0.0
        return sum(float(row.get(key, 0)) for row in rows) / len(rows)

    def _strategy_score(
        self,
        sample_count: int,
        tracked_count: int,
        positive_count: int,
        avg_return: float,
        avg_high: float,
        avg_low: float,
    ) -> int:
        if tracked_count == 0:
            return 0
        positive_rate = positive_count / tracked_count
        score = 45
        score += (positive_rate - 0.5) * 50
        score += avg_return * 6
        score += avg_high * 3
        score += avg_low * 2
        if tracked_count < max(2, sample_count * 0.5):
            score -= 8
        if sample_count > 20 and positive_rate < 0.5:
            score -= 8
        return max(0, min(100, round(score)))

    def _best_sector(self, rows: list[dict]) -> str:
        if not rows:
            return "--"
        sectors: dict[str, dict[str, float]] = {}
        for row in rows:
            sector = row.get("sector", "未分组")
            sectors.setdefault(sector, {"count": 0, "total": 0.0})
            sectors[sector]["count"] += 1
            sectors[sector]["total"] += float(row.get("next_return_pct", 0))
        sector, stats = max(sectors.items(), key=lambda item: (item[1]["total"] / max(1, item[1]["count"]), item[1]["count"]))
        return f"{sector} {stats['count']:.0f}只 / 均{stats['total'] / max(1, stats['count']):.2f}%"

    def _suggestion(
        self,
        sample_count: int,
        tracked_count: int,
        positive_count: int,
        avg_return: float,
        avg_high: float,
        avg_low: float,
    ) -> str:
        if tracked_count == 0:
            return "等待次日样本完成后评估"
        positive_rate = positive_count / tracked_count
        if sample_count >= 10 and positive_rate < 0.45:
            return "样本偏多但胜率低，建议提高主买或成交额门槛"
        if sample_count <= 3 and positive_rate >= 0.6 and avg_return > 0:
            return "样本偏少但质量不错，可小幅放宽启动门槛"
        if avg_high > 1.5 and avg_return < 0.3:
            return "盘中最高收益好但回落明显，更适合盘中止盈"
        if avg_low < -2:
            return "次日低点回撤偏大，隔夜风险需要降低"
        if positive_rate >= 0.6 and avg_return > 0.5:
            return "规则表现较好，保持当前强关注门槛"
        return "继续观察，暂不建议大幅调参"
