from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from historical_backtest import MinuteBar
from market_clock import ashare_session, is_trading_date
from market_quotes import fetch_market_quotes
from openclaw_review import run_openclaw_limit_up_focus_review
from user_preferences import normalize_code

EASTMONEY_ZT_POOL_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_TRENDS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
OFFICIAL_BUY_START_DATE = "2026-06-17"


@dataclass
class CacheEntry:
    ts: float
    payload: dict[str, Any]


class LimitUpMonitor:
    def __init__(self, data_dir: Path, ttl_sec: int = 20) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_sec = ttl_sec
        self._cache: CacheEntry | None = None

    def payload(self, watchlist: set[str] | None = None, force: bool = False, notify: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._cache and now - self._cache.ts < self.ttl_sec:
            return dict(self._cache.payload)

        session = ashare_session()
        trade_date = _latest_trading_date(str(session.get("date") or ""))
        prev_date = _previous_trading_date(trade_date)
        raw_zt_pool, zt_error = self._fetch_limit_pool(trade_date)
        raw_prev_pool, prev_error = self._fetch_limit_pool(prev_date)
        zt_pool = _filter_tradeable_pool(raw_zt_pool)
        prev_pool = _filter_tradeable_pool(raw_prev_pool)
        prev_first_board = {item["code"] for item in prev_pool if int(item.get("streak") or 1) <= 1}
        watchlist = watchlist or set()
        quote_codes = sorted({item["code"] for item in zt_pool} | prev_first_board | watchlist)
        quote_payload = fetch_market_quotes(quote_codes[:80]) if quote_codes else {"quotes": {}}
        quotes = quote_payload.get("quotes") or {}

        auction = _build_auction_candidates(quotes, prev_first_board, watchlist)
        sectors = _rank_sectors(zt_pool)
        ladders = _build_ladders(zt_pool, prev_first_board)
        signals = _build_signals(auction, ladders["second_board"], sectors)
        summary = {
            "zt_count": len(zt_pool),
            "first_board": len(ladders["first_board"]),
            "second_board": len(ladders["second_board"]),
            "height": max([int(item.get("streak") or 1) for item in zt_pool] or [0]),
            "strong_sector_count": len([item for item in sectors if item["limit_count"] >= 3]),
            "strong_auction_count": len(auction),
            "signal_count": len(signals),
            "excluded_count": len(raw_zt_pool) - len(zt_pool),
        }
        payload = {
            "date": trade_date,
            "previous_date": prev_date,
            "session": session,
            "source": "eastmoney",
            "ts": now,
            "summary": summary,
            "auction": auction,
            "sectors": sectors,
            "ladders": ladders,
            "signals": signals,
            "errors": [error for error in [zt_error, prev_error, quote_payload.get("error")] if error],
        }
        self._write_snapshot(payload)
        self._cache = CacheEntry(now, payload)
        return payload

    def build_tomorrow_focus(self, trade_date: str | None = None, force: bool = False) -> dict[str, Any]:
        session = ashare_session()
        current_date = trade_date or _focus_review_date_for_session(session)
        next_date = _next_trading_date(current_date)
        existing = self._load_focus(current_date)
        raw_pool, error = self._fetch_limit_pool(current_date)
        pool = _filter_tradeable_pool(raw_pool)
        sectors = _rank_sectors(pool)
        focus = _build_tomorrow_focus(pool, sectors)
        payload = {
            "date": current_date,
            "next_date": next_date,
            "source": "eastmoney",
            "ts": time.time(),
            "summary": {
                "zt_count": len(pool),
                "focus_count": len(focus),
                "watch_count": len(pool),
                "strong_sector_count": len([item for item in sectors if item["limit_count"] >= 3]),
                "height": max([int(item.get("streak") or 1) for item in pool] or [0]),
                "excluded_count": len(raw_pool) - len(pool),
            },
            "focus": focus,
            "watch_pool": pool,
            "sectors": sectors,
            "errors": [error] if error else [],
        }
        payload = _preserve_openclaw_focus_review(payload, existing)
        self._write_focus(payload)
        return payload

    def review_tomorrow_focus_with_openclaw(self, trade_date: str | None = None, max_items: int = 120, timeout: int | None = None) -> dict[str, Any]:
        session = ashare_session()
        current_date = trade_date or _focus_review_date_for_session(session)
        focus_payload = self._load_focus(current_date) or self.build_tomorrow_focus(current_date, True)
        focus = _filter_tradeable_pool(list(focus_payload.get("focus") or []))
        watch_pool = _filter_tradeable_pool(list(focus_payload.get("watch_pool") or []))
        sectors = list(focus_payload.get("sectors") or [])
        limit = max(3, min(int(max_items or 120), 120))
        context = _build_openclaw_batch_context(focus_payload, focus[:limit], watch_pool, sectors)
        result = run_openclaw_limit_up_focus_review(context, timeout=timeout)
        selected_codes = {str(item.get("code")) for item in (result.get("items") or []) if isinstance(item, dict)}
        focus_codes = {str(item.get("code")) for item in focus}
        extra_focus = [item for item in watch_pool if str(item.get("code")) in selected_codes and str(item.get("code")) not in focus_codes]
        reviewed = [_merge_openclaw_focus_item(item, result) for item in [*focus, *extra_focus]]
        reviewed_by_code = {str(item.get("code")): item for item in reviewed}
        merged_focus = [reviewed_by_code.get(str(item.get("code")), item) for item in [*focus, *extra_focus]]
        merged_focus = sorted(merged_focus, key=_openclaw_focus_sort_key, reverse=True)
        focus_payload["focus"] = merged_focus
        focus_payload["openclaw_review"] = {
            "ts": time.time(),
            "date": current_date,
            "max_items": limit,
            "mode": "batch_agent",
            "available": result.get("available", False),
            "summary": result.get("summary") or result.get("error") or "",
            "market_view": result.get("market_view") or "",
            "elapsed_ms": result.get("elapsed_ms", 0),
            "reviewed_count": len(result.get("items") or []),
            "core_count": len([item for item in merged_focus if item.get("openclaw_tier") == "core"]),
            "watch_count": len([item for item in merged_focus if item.get("openclaw_tier") == "watch"]),
            "avoid_count": len([item for item in merged_focus if item.get("openclaw_tier") == "avoid"]),
            "unavailable_count": len([item for item in merged_focus if item.get("openclaw_tier") == "unavailable"]),
            "results": result.get("items") or [],
        }
        focus_payload["summary"]["focus_count"] = len(merged_focus)
        self._write_focus(focus_payload)
        return focus_payload

    def load_focus_for_monitor_date(self, monitor_date: str | None = None) -> dict[str, Any]:
        session = ashare_session()
        current_date = monitor_date or _latest_trading_date(str(session.get("date") or ""))
        source_date = _previous_trading_date(current_date)
        path = self._focus_path(source_date)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
            except (OSError, json.JSONDecodeError):
                pass
        return self.build_tomorrow_focus(source_date)

    def monitor_yesterday_pool(self, monitor_date: str | None = None, force: bool = False) -> dict[str, Any]:
        session = ashare_session()
        current_date = monitor_date or _latest_trading_date(str(session.get("date") or ""))
        focus_payload = self.load_focus_for_monitor_date(current_date)
        watch_pool = _filter_tradeable_pool(focus_payload.get("watch_pool") or [])
        codes = [str(item.get("code") or "") for item in watch_pool if item.get("code")]
        try:
            quotes_payload = fetch_market_quotes(codes) if codes else {"quotes": {}}
        except Exception as error:
            quotes_payload = {"quotes": {}, "error": f"行情获取失败: {error.__class__.__name__}: {error}"}
        raw_today_pool, zt_error = self._fetch_limit_pool(current_date)
        today_pool = _filter_tradeable_pool(raw_today_pool)
        stale_error = ""
        watch_codes = {str(item.get("code") or "") for item in watch_pool if item.get("code")}
        today_codes = {str(item.get("code") or "") for item in today_pool if item.get("code")}
        if watch_codes and today_codes and len(watch_codes & today_codes) / max(1, len(watch_codes)) > 0.8:
            today_pool = []
            stale_error = f"{current_date} 涨停池疑似返回昨日数据，已忽略今日封板状态"
        today_by_code = {item["code"]: item for item in today_pool}
        today_sectors = _rank_sectors(today_pool)
        sector_strength = {item["sector"]: item for item in today_sectors}
        focus_by_code = {str(item.get("code")): item for item in _filter_tradeable_pool(focus_payload.get("focus") or [])}
        kline_codes = _select_kline_codes(watch_pool, quotes_payload.get("quotes") or {}, focus_by_code)
        kline_by_code, kline_errors = _fetch_intraday_kline_signals(kline_codes, current_date)
        rows = _build_next_day_rows(watch_pool, quotes_payload.get("quotes") or {}, today_by_code, sector_strength, focus_by_code, kline_by_code)
        opportunity_signals = sorted(
            [item for item in rows if item.get("action") == "BUY" and item.get("openclaw_tier") != "avoid"],
            key=_official_candidate_sort_key,
            reverse=True,
        )
        allow_official_lock = _allow_official_buy_lock(session, current_date)
        buy_signals = self._lock_official_buy_signals(current_date, opportunity_signals, rows, allow_official_lock, session)
        kline_source_counts: dict[str, int] = {}
        for signal in kline_by_code.values():
            source = str(signal.get("source") or "unknown")
            kline_source_counts[source] = kline_source_counts.get(source, 0) + 1
        data_quality = {
            "quote_count": len(quotes_payload.get("quotes") or {}),
            "watch_count": len(watch_pool),
            "kline_requested_count": len(kline_codes),
            "kline_ready_count": len([item for item in kline_by_code.values() if item.get("available")]),
            "kline_source_counts": kline_source_counts,
            "today_pool_count": len(today_pool),
            "today_pool_ignored": bool(stale_error),
            "updated_at": time.time(),
            "source": "eastmoney+quotes+kline",
        }
        payload = {
            "date": current_date,
            "source_date": focus_payload.get("date"),
            "source": "eastmoney",
            "ts": time.time(),
            "session": session,
            "phase": _monitor_phase(session, len(buy_signals)),
            "focus": _filter_tradeable_pool(focus_payload.get("focus") or []),
            "watch_pool": watch_pool,
            "today_pool": today_pool,
            "rows": rows,
            "buy_signals": buy_signals,
            "opportunity_signals": opportunity_signals,
            "today_sectors": today_sectors,
            "data_quality": data_quality,
            "summary": {
                "watch_count": len(watch_pool),
                "today_limit_count": len(today_pool),
                "active_count": len([item for item in rows if item.get("state") in {"冲板临界", "首封确认", "回封确认", "分时确认", "开盘确认"}]),
                "buy_signal_count": len(buy_signals),
                "remaining_buy_slots": None,
                "opportunity_count": len(opportunity_signals),
                "sealed_count": len([item for item in rows if item.get("sealed_today")]),
                "excluded_count": len(focus_payload.get("watch_pool") or []) - len(watch_pool),
            },
            "errors": [error for error in [zt_error, stale_error, quotes_payload.get("error"), *kline_errors] if error],
        }
        self._write_next_day(payload)
        self._write_next_day_review(payload)
        return payload

    def _official_buy_path(self, trade_date: str) -> Path:
        return self.data_dir / f"limit_up_official_buys_{trade_date}.json"

    def update_official_execution(self, trade_date: str, code: str, status: str, price: float = 0, shares: int = 0, note: str = "") -> dict[str, Any]:
        path = self._official_buy_path(trade_date)
        payload = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
        normalized = normalize_code(code)
        found = False
        for item in items:
            if str(item.get("code") or "") != normalized:
                continue
            item["execution_status"] = status
            item["execution_price"] = price or item.get("execution_price") or item.get("entry_price") or item.get("price")
            item["execution_shares"] = shares or item.get("execution_shares") or 0
            item["execution_note"] = note
            item["execution_updated_at"] = time.time()
            found = True
        if not found:
            items.append({
                "code": normalized,
                "execution_status": status,
                "execution_price": price,
                "execution_shares": shares,
                "execution_note": note,
                "execution_updated_at": time.time(),
            })
        payload["date"] = trade_date
        payload["items"] = items
        payload["codes"] = [str(code) for code in payload.get("codes") or [] if str(code)] or [str(item.get("code")) for item in items if item.get("code")]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.data_dir / "limit_up_official_buys_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def _lock_official_buy_signals(
        self,
        trade_date: str,
        opportunities: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        allow_new_locks: bool = True,
        session: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if trade_date < OFFICIAL_BUY_START_DATE:
            for row in rows:
                row["official_buy"] = False
                row["official_rank"] = 0
            return []
        path = self._official_buy_path(trade_date)
        locked_codes: list[str] = []
        locked_items: dict[str, dict[str, Any]] = {}
        locked_at = time.time()
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    locked_codes = [str(item) for item in payload.get("codes") or [] if str(item)]
                    locked_items = {str(item.get("code")): item for item in payload.get("items") or [] if isinstance(item, dict)}
                    locked_at = _number(payload.get("locked_at")) or locked_at
            except (OSError, json.JSONDecodeError):
                locked_codes = []
                locked_items = {}
        if allow_new_locks:
            row_by_code = {str(item.get("code") or ""): item for item in rows if item.get("code")}
            next_locked_codes = []
            for code in locked_codes:
                execution = str((locked_items.get(code) or {}).get("execution_status") or "")
                if execution in {"missed", "abandoned"}:
                    continue
                if execution != "filled" and _should_release_official_lock(row_by_code.get(code)):
                    continue
                next_locked_codes.append(code)
            locked_codes = next_locked_codes
            locked_items = {code: item for code, item in locked_items.items() if code in locked_codes}
        sector_counts: dict[str, int] = {}
        for code in locked_codes:
            item = locked_items.get(code) or {}
            sector = str(item.get("sector") or "")
            if sector:
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if allow_new_locks:
            for item in opportunities:
                code = str(item.get("code") or "")
                sector = str(item.get("sector") or "")
                if (
                    code
                    and code not in locked_codes
                    and not item.get("buy_unavailable")
                    and sector_counts.get(sector, 0) < 2
                    and _official_candidate_allowed(item, session)
                ):
                    locked_codes.append(code)
                    sector_counts[sector] = sector_counts.get(sector, 0) + 1
        rank_by_code = {code: index + 1 for index, code in enumerate(locked_codes)}
        by_code = {str(item.get("code")): item for item in opportunities}
        official = []
        for row in rows:
            code = str(row.get("code") or "")
            rank = rank_by_code.get(code)
            row["official_buy"] = bool(rank)
            row["official_rank"] = rank or 0
            if rank:
                old = locked_items.get(code) or {}
                row["official_locked_at"] = old.get("locked_at") or locked_at
                row["official_trigger_time"] = old.get("trigger_time") or time.strftime("%H:%M:%S")
                row["official_trigger_price"] = old.get("trigger_price") or row.get("price")
                row["official_entry_price"] = old.get("entry_price") or _entry_price(row)
                row["official_reason"] = old.get("reason") or _official_reason(row)
                row["execution_status"] = old.get("execution_status") or "triggered"
                row["execution_price"] = old.get("execution_price") or 0
                row["execution_shares"] = old.get("execution_shares") or 0
                row["execution_note"] = old.get("execution_note") or ""
            if rank:
                official.append(row)
        snapshot = {
            "date": trade_date,
            "locked_at": locked_at,
            "codes": locked_codes,
            "items": [
                {
                    "code": item.get("code"),
                    "name": item.get("name"),
                    "sector": item.get("sector"),
                    "official_rank": item.get("official_rank"),
                    "locked_at": item.get("official_locked_at"),
                    "trigger_time": item.get("official_trigger_time"),
                    "trigger_price": item.get("official_trigger_price"),
                    "entry_price": item.get("official_entry_price"),
                    "reason": item.get("official_reason"),
                    "state": item.get("state"),
                    "score": item.get("score"),
                    "price": item.get("price"),
                    "reasons": item.get("reasons"),
                    "execution_status": item.get("execution_status") or "triggered",
                    "execution_price": item.get("execution_price") or 0,
                    "execution_shares": item.get("execution_shares") or 0,
                    "execution_note": item.get("execution_note") or "",
                    "execution_updated_at": item.get("execution_updated_at") or 0,
                }
                for item in official
            ],
        }
        if allow_new_locks or path.exists():
            path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            (self.data_dir / "limit_up_official_buys_latest.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        return official

    def _fetch_limit_pool(self, trade_date: str) -> tuple[list[dict[str, Any]], str]:
        query = urlencode(
            {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": 0,
                "pagesize": 500,
                "sort": "fbt:asc",
                "date": trade_date.replace("-", ""),
            }
        )
        request = Request(
            f"{EASTMONEY_ZT_POOL_URL}?{query}",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://quote.eastmoney.com/",
            },
        )
        try:
            with urlopen(request, timeout=6) as response:
                payload = json.loads(response.read().decode("utf-8"))
            rows = ((payload.get("data") or {}).get("pool") or []) if isinstance(payload, dict) else []
            return [_normalize_limit_row(row) for row in rows if isinstance(row, dict)], ""
        except Exception as error:
            return [], f"{trade_date} 涨停池获取失败: {error.__class__.__name__}: {error}"

    def _write_snapshot(self, payload: dict[str, Any]) -> None:
        target = self.data_dir / "limit_up_state.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_focus(self, payload: dict[str, Any]) -> None:
        self._focus_path(str(payload.get("date"))).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.data_dir / "limit_up_focus_latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_next_day(self, payload: dict[str, Any]) -> None:
        (self.data_dir / "limit_up_next_day_state.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_next_day_review(self, payload: dict[str, Any]) -> None:
        rows = payload.get("rows") or []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in rows:
            grouped.setdefault(str(item.get("openclaw_tier") or "rule"), []).append(item)
        stats = {}
        for tier, items in grouped.items():
            stats[tier] = {
                "count": len(items),
                "buy_count": len([item for item in items if item.get("action") == "BUY"]),
                "sealed_count": len([item for item in items if item.get("sealed_today")]),
                "avg_change_pct": round(sum(_number(item.get("change_pct")) for item in items) / max(1, len(items)), 2),
                "avg_open_pct": round(sum(_number(item.get("open_pct")) for item in items) / max(1, len(items)), 2),
            }
        review = {
            "date": payload.get("date"),
            "source_date": payload.get("source_date"),
            "ts": time.time(),
            "summary": payload.get("summary") or {},
            "tiers": stats,
            "top_rows": _review_archive_rows(rows),
        }
        (self.data_dir / f"limit_up_next_day_review_{payload.get('source_date')}_{payload.get('date')}.json").write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.data_dir / "limit_up_next_day_review_latest.json").write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    def _focus_path(self, trade_date: str) -> Path:
        return self.data_dir / f"limit_up_focus_{trade_date}.json"

    def _load_focus(self, trade_date: str) -> dict[str, Any] | None:
        path = self._focus_path(trade_date)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except (OSError, json.JSONDecodeError):
            return None


def _normalize_limit_row(row: dict[str, Any]) -> dict[str, Any]:
    code = normalize_code(str(row.get("c") or ""))
    streak_info = row.get("zttj") if isinstance(row.get("zttj"), dict) else {}
    streak = int(row.get("lbc") or streak_info.get("ct") or 1)
    return {
        "code": code,
        "name": str(row.get("n") or code),
        "sector": str(row.get("hybk") or "未分组"),
        "price": _price(row.get("p")),
        "change_pct": round(_number(row.get("zdp")), 2),
        "amount": _number(row.get("amount")),
        "float_market_value": _number(row.get("ltsz")),
        "turnover_rate": round(_number(row.get("hs")), 2),
        "streak": max(1, streak),
        "first_limit_time": _time_label(row.get("fbt")),
        "last_limit_time": _time_label(row.get("lbt")),
        "seal_amount": _number(row.get("fund")),
        "open_board_count": int(_number(row.get("zbc"))),
        "days": int(streak_info.get("days") or streak),
    }


def _filter_tradeable_pool(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in pool if _is_tradeable_main_board(str(item.get("code") or ""))]


def _review_archive_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    priority = sorted(
        rows,
        key=lambda item: (
            bool(item.get("official_buy")),
            item.get("action") == "BUY",
            item.get("action") == "WATCH",
            _number(item.get("score")),
        ),
        reverse=True,
    )
    for item in priority:
        code = str(item.get("code") or "")
        if not code or code in seen:
            continue
        selected.append(item)
        seen.add(code)
        if len(selected) >= 120:
            break
    return selected


def _is_tradeable_main_board(code: str) -> bool:
    if code.startswith(("300", "301", "688", "689", "920", "8", "4")):
        return False
    return True


def _build_auction_candidates(quotes: dict[str, Any], prev_first_board: set[str], watchlist: set[str]) -> list[dict[str, Any]]:
    rows = []
    for code, quote in quotes.items():
        prev_close = _number(quote.get("prev_close"))
        open_price = _number(quote.get("open")) or _number(quote.get("price"))
        if not code or prev_close <= 0 or open_price <= 0:
            continue
        open_pct = round((open_price / prev_close - 1) * 100, 2)
        amount = _number(quote.get("amount"))
        volume = _number(quote.get("volume"))
        if open_pct < 2 and code not in prev_first_board and code not in watchlist:
            continue
        reasons = []
        score = 0
        if open_pct >= 3:
            score += 30
            reasons.append("高开超过3%")
        elif open_pct >= 2:
            score += 18
            reasons.append("温和高开")
        if amount >= 50_000_000:
            score += 22
            reasons.append("成交额放大")
        elif amount >= 15_000_000:
            score += 12
            reasons.append("竞价/早盘有量")
        if code in prev_first_board:
            score += 28
            reasons.append("昨日首板")
        if code in watchlist:
            score += 8
            reasons.append("关注池")
        rows.append(
            {
                "code": code,
                "name": quote.get("name") or code,
                "price": round(_number(quote.get("price")) or open_price, 3),
                "open": round(open_price, 3),
                "prev_close": round(prev_close, 3),
                "open_pct": open_pct,
                "change_pct": round(_number(quote.get("change_pct")), 2),
                "amount": amount,
                "volume": volume,
                "score": score,
                "prev_first_board": code in prev_first_board,
                "watching": code in watchlist,
                "reasons": reasons,
            }
        )
    return sorted(rows, key=lambda item: (item["score"], item["amount"]), reverse=True)[:30]


def _rank_sectors(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in pool:
        grouped.setdefault(item.get("sector") or "未分组", []).append(item)
    rows = []
    for sector, items in grouped.items():
        early_count = len([item for item in items if str(item.get("first_limit_time", "")) <= "10:00"])
        max_streak = max([int(item.get("streak") or 1) for item in items] or [1])
        seal_amount = sum(_number(item.get("seal_amount")) for item in items)
        leader = sorted(items, key=lambda item: (int(item.get("streak") or 1), -_time_sort(item.get("first_limit_time")), _number(item.get("seal_amount"))), reverse=True)[0]
        score = len(items) * 10 + early_count * 4 + max_streak * 5 + min(20, seal_amount / 100_000_000)
        rows.append(
            {
                "sector": sector,
                "limit_count": len(items),
                "early_count": early_count,
                "max_streak": max_streak,
                "seal_amount": seal_amount,
                "score": round(score, 1),
                "leader": leader,
                "stocks": items[:12],
            }
        )
    return sorted(rows, key=lambda item: item["score"], reverse=True)


def _build_ladders(pool: list[dict[str, Any]], prev_first_board: set[str]) -> dict[str, list[dict[str, Any]]]:
    first_board = [item for item in pool if int(item.get("streak") or 1) <= 1]
    second_board = [item for item in pool if int(item.get("streak") or 1) == 2 or item["code"] in prev_first_board]
    high_board = [item for item in pool if int(item.get("streak") or 1) >= 3]
    key = lambda item: (-int(item.get("streak") or 1), item.get("first_limit_time") or "99:99")
    return {
        "first_board": sorted(first_board, key=key),
        "second_board": sorted(second_board, key=key),
        "high_board": sorted(high_board, key=key),
    }


def _build_signals(auction: list[dict[str, Any]], second_board: list[dict[str, Any]], sectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sector_score = {item["sector"]: item for item in sectors}
    second_codes = {item["code"]: item for item in second_board}
    rows = []
    for item in auction:
        board = second_codes.get(item["code"])
        sector = board.get("sector") if board else ""
        theme = sector_score.get(sector) if sector else None
        score = item["score"] + (28 if board else 0) + (20 if theme and theme["limit_count"] >= 3 else 0)
        if score < 58:
            continue
        reasons = list(item.get("reasons") or [])
        if board:
            reasons.append("一进二梯队")
        if theme and theme["limit_count"] >= 3:
            reasons.append(f"{sector}板块联动")
        rows.append(
            {
                "code": item["code"],
                "name": item["name"],
                "sector": sector or "未分组",
                "action": "重点打板" if board and theme and theme["limit_count"] >= 3 else "观察确认",
                "score": round(score, 1),
                "price": item["price"],
                "open_pct": item["open_pct"],
                "amount": item["amount"],
                "reasons": reasons,
                "risk_note": "只在封单回补/板块继续增强时执行，炸板回落放弃。",
            }
        )
    return sorted(rows, key=lambda item: item["score"], reverse=True)[:20]


def _build_tomorrow_focus(pool: list[dict[str, Any]], sectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sector_rank = {item["sector"]: index + 1 for index, item in enumerate(sectors)}
    sector_strength = {item["sector"]: item for item in sectors}
    rows = []
    for item in pool:
        sector = str(item.get("sector") or "未分组")
        theme = sector_strength.get(sector, {})
        streak = int(item.get("streak") or 1)
        first_time = str(item.get("first_limit_time") or "99:99")
        seal_amount = _number(item.get("seal_amount"))
        open_count = int(item.get("open_board_count") or 0)
        score = 40
        reasons = []
        if streak == 1:
            score += 18
            reasons.append("首板，明日一进二观察")
        elif streak == 2:
            score += 12
            reasons.append("二板高度，明日看分歧承接")
        else:
            score += 6
            reasons.append(f"{streak}板高标，偏情绪观察")
        if first_time <= "10:00":
            score += 16
            reasons.append("早盘封板")
        if int(theme.get("limit_count") or 0) >= 3:
            score += 18
            reasons.append(f"{sector}板块联动")
        if seal_amount >= 100_000_000:
            score += 10
            reasons.append("封单强")
        if open_count >= 3:
            score -= 12
            reasons.append("炸板次数偏多")
        rows.append(
            {
                **item,
                "focus_score": round(score, 1),
                "sector_rank": sector_rank.get(sector, 99),
                "next_day_plan": "一进二重点" if streak == 1 and score >= 72 else "观察承接" if score >= 60 else "只看不做",
                "focus_reasons": reasons,
            }
        )
    return sorted(rows, key=lambda item: (item["focus_score"], -item["sector_rank"], _number(item.get("seal_amount"))), reverse=True)[:30]


def _preserve_openclaw_focus_review(payload: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    if not existing or existing.get("date") != payload.get("date") or not existing.get("openclaw_review"):
        return payload
    preserved_keys = [
        "openclaw_tier",
        "openclaw_score",
        "openclaw_action",
        "openclaw_risk_level",
        "openclaw_summary",
        "openclaw_reasons",
        "openclaw_risks",
        "openclaw_review",
    ]
    existing_focus = existing.get("focus") if isinstance(existing.get("focus"), list) else []
    by_code = {str(item.get("code")): item for item in existing_focus if isinstance(item, dict)}
    seen: set[str] = set()
    merged = []
    for item in payload.get("focus") or []:
        code = str(item.get("code"))
        seen.add(code)
        old = by_code.get(code)
        if old:
            next_item = {**item}
            for key in preserved_keys:
                if key in old:
                    next_item[key] = old[key]
            if old.get("next_day_plan") and old.get("openclaw_tier") in {"core", "watch", "avoid"}:
                next_item["next_day_plan"] = old["next_day_plan"]
            if old.get("focus_reasons") and old.get("openclaw_summary"):
                next_item["focus_reasons"] = old["focus_reasons"]
            merged.append(next_item)
        else:
            merged.append(item)
    for old in existing_focus:
        code = str(old.get("code"))
        if code and code not in seen and old.get("openclaw_tier") in {"core", "watch", "avoid"}:
            merged.append(old)
    payload["focus"] = sorted(merged, key=_openclaw_focus_sort_key, reverse=True)
    payload["openclaw_review"] = existing.get("openclaw_review")
    payload["summary"]["focus_count"] = len(payload["focus"])
    return payload


def _build_openclaw_batch_context(focus_payload: dict[str, Any], focus: list[dict[str, Any]], watch_pool: list[dict[str, Any]], sectors: list[dict[str, Any]]) -> dict[str, Any]:
    pool_limit = int(os.environ.get("OPENCLAW_LIMIT_UP_POOL_LIMIT", str(len(watch_pool) or 120)))
    sector_limit = int(os.environ.get("OPENCLAW_LIMIT_UP_SECTOR_LIMIT", "12"))
    return {
        "mode": "limit_up_tomorrow_focus",
        "task": "收盘后由 OpenClaw agent 全量扫描今日涨停板，筛选明日核心盯盘、观察池和风险剔除。",
        "date": focus_payload.get("date"),
        "next_date": focus_payload.get("next_date"),
        "market_summary": focus_payload.get("summary") or {},
        "rule_focus": [_compact_focus_item(item) for item in focus],
        "limit_up_pool": [_compact_pool_item(item) for item in watch_pool[:max(3, pool_limit)]],
        "sectors": [_compact_sector_item(item) for item in sectors[:max(2, sector_limit)]],
    }


def _compact_focus_item(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "code",
        "name",
        "sector",
        "amount",
        "streak",
        "first_limit_time",
        "seal_amount",
        "open_board_count",
        "focus_score",
        "sector_rank",
        "next_day_plan",
        "focus_reasons",
    ]
    return {key: item.get(key) for key in keys if key in item}


def _compact_pool_item(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "code",
        "name",
        "sector",
        "streak",
        "first_limit_time",
        "seal_amount",
        "open_board_count",
    ]
    return {key: item.get(key) for key in keys if key in item}


def _compact_sector_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "sector": item.get("sector"),
        "limit_count": item.get("limit_count"),
        "early_count": item.get("early_count"),
        "max_streak": item.get("max_streak"),
        "score": item.get("score"),
        "leader": _compact_pool_item(item.get("leader") or {}),
        "stocks": [_compact_pool_item(stock) for stock in (item.get("stocks") or [])[:3]],
    }


def _merge_openclaw_focus_item(item: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    by_code = {str(row.get("code")): row for row in (result.get("items") or []) if isinstance(row, dict)}
    decision = by_code.get(str(item.get("code")))
    if not result.get("available"):
        previous_tier = str(item.get("openclaw_tier") or "")
        fallback_tier = previous_tier if previous_tier in {"core", "watch", "avoid"} else "rule"
        return {
            **item,
            "openclaw_tier": fallback_tier,
            "openclaw_summary": item.get("openclaw_summary") or str(result.get("error") or result.get("summary") or "OpenClaw 超时，暂按规则排序。"),
        }
    if not decision:
        return {**item, "openclaw_tier": "rule", "openclaw_summary": "规则候选，OpenClaw 未列入重点清单。"}
    tier = str(decision.get("tier") or "watch")
    summary = str(decision.get("summary") or "")
    reasons = _string_list(decision.get("reasons"))
    risks = _string_list(decision.get("risks"))
    next_reasons = list(item.get("focus_reasons") or [])
    if summary:
        next_reasons.insert(0, f"OpenClaw: {summary[:42]}")
    return {
        **item,
        "openclaw_tier": tier,
        "openclaw_score": int(_number(decision.get("confidence"))),
        "openclaw_action": decision.get("action") or "",
        "openclaw_risk_level": decision.get("risk_level") or "",
        "openclaw_summary": summary,
        "openclaw_reasons": reasons,
        "openclaw_risks": risks,
        "openclaw_review": decision,
        "next_day_plan": str(decision.get("next_day_plan") or _tier_plan(tier, str(item.get("next_day_plan") or "观察承接"))),
        "focus_reasons": next_reasons[:6],
    }


def _tier_plan(tier: str, fallback: str) -> str:
    if tier == "core":
        return "核心盯盘"
    if tier == "watch":
        return "Agent观察"
    if tier == "avoid":
        return "风险剔除"
    return fallback


def _openclaw_focus_sort_key(item: dict[str, Any]) -> tuple[int, float, float, float]:
    tier_rank = {"core": 5, "watch": 4, "rule": 3, "unavailable": 2, "avoid": 1}
    return (
        tier_rank.get(str(item.get("openclaw_tier") or ""), 2),
        _number(item.get("openclaw_score")),
        _number(item.get("focus_score")),
        _number(item.get("seal_amount")),
    )


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()][:8]
    if value:
        return [str(value)]
    return []


def _monitor_phase(session: dict[str, Any], official_count: int = 0) -> dict[str, Any]:
    code = str(session.get("code") or "")
    labels = {
        "PRE_MARKET": "等待开盘",
        "CALL_AUCTION": "集合竞价观察",
        "PRE_OPEN": "开盘计划",
        "MORNING": "主交易窗口",
        "LUNCH": "午间休息",
        "AFTERNOON": "回封/核心窗口",
        "CLOSING_AUCTION": "收盘确认",
        "POST_CLOSE": "等待复盘",
        "CLOSED": "等待收盘复盘",
    }
    return {"code": code or "UNKNOWN", "label": labels.get(code, str(session.get("label") or "盯盘中")), "remaining_slots": None}


def _allow_official_buy_lock(session: dict[str, Any], trade_date: str) -> bool:
    live_codes = {"MORNING", "AFTERNOON", "CLOSING_AUCTION"}
    return bool(
        trade_date >= OFFICIAL_BUY_START_DATE
        and str(session.get("date") or "") == trade_date
        and str(session.get("code") or "") in live_codes
    )


def _select_kline_codes(watch_pool: list[dict[str, Any]], quotes: dict[str, Any], focus_by_code: dict[str, dict[str, Any]]) -> list[str]:
    selected: list[tuple[float, str]] = []
    for source in watch_pool:
        code = str(source.get("code") or "")
        if not code:
            continue
        quote = quotes.get(code) or {}
        prev_close = _number(quote.get("prev_close")) or _number(source.get("price"))
        open_price = _number(quote.get("open"))
        open_pct = round((open_price / prev_close - 1) * 100, 2) if prev_close and open_price else 0
        change_pct = _number(quote.get("change_pct"))
        tier = str((focus_by_code.get(code) or {}).get("openclaw_tier") or "")
        if tier in {"core", "watch"} or open_pct >= 1.5 or change_pct >= 3.5:
            tier_weight = 20 if tier == "core" else 12 if tier == "watch" else 0
            selected.append((tier_weight + open_pct + change_pct, code))
    return [code for _, code in sorted(selected, reverse=True)[:24]]


def _fetch_intraday_kline_signals(codes: list[str], trade_date: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    signals: dict[str, dict[str, Any]] = {}
    errors = []
    if not codes:
        return signals, []
    _update_tdx_minute_cache(codes, trade_date)

    def fetch_one(code: str) -> tuple[str, dict[str, Any]]:
        bars, source = _fetch_fast_minute_bars_with_meta(_code_symbol(code), trade_date)
        signal = _analyze_intraday_kline(bars)
        signal["source"] = source
        return code, signal

    workers = max(1, min(len(codes), int(os.environ.get("LIMIT_UP_KLINE_WORKERS", "5") or 5)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_one, code): code for code in codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                next_code, signal = future.result()
                signals[next_code] = signal
            except Exception as error:
                errors.append(f"{code} 分时K线获取失败: {error.__class__.__name__}: {error}")
    if errors:
        return signals, [f"部分分时K线暂不可用: {len(errors)}/{len(codes)} 只"]
    return signals, []


def _update_tdx_minute_cache(codes: list[str], trade_date: str) -> None:
    url = os.environ.get("MARKET_HTTP_URL") or "http://127.0.0.1:9002/ticks"
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return
    ticks = payload.get("ticks") if isinstance(payload, dict) else payload
    if not isinstance(ticks, list):
        return
    code_set = set(codes)
    for tick in ticks:
        if not isinstance(tick, dict):
            continue
        code = str(tick.get("code") or "")
        if code not in code_set:
            continue
        price = _number(tick.get("price"))
        prev_close = _number(tick.get("prev_close"))
        ts = _number(tick.get("ts")) or time.time()
        if price <= 0 or prev_close <= 0:
            continue
        minute = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        if not minute.startswith(trade_date):
            continue
        symbol = _code_symbol(code)
        bars = _read_minute_bars_cache(symbol, trade_date) or []
        next_bar = MinuteBar(
            ts=minute,
            open=price,
            close=price,
            high=price,
            low=price,
            volume=_number(tick.get("volume")) / 100,
            amount=_number(tick.get("turnover")),
            prev_close=prev_close,
        )
        if bars and bars[-1].ts[:16] == minute:
            previous = bars[-1]
            bars[-1] = MinuteBar(
                ts=minute,
                open=previous.open or price,
                close=price,
                high=max(previous.high or price, price),
                low=min(previous.low or price, price),
                volume=max(previous.volume, next_bar.volume),
                amount=max(previous.amount, next_bar.amount),
                prev_close=previous.prev_close or prev_close,
            )
        else:
            bars.append(next_bar)
        _write_minute_bars_cache(symbol, trade_date, bars[-260:], source="tdx")


def _fetch_fast_minute_bars(symbol: str, trade_date: str) -> list[MinuteBar]:
    bars, _ = _fetch_fast_minute_bars_with_meta(symbol, trade_date)
    return bars


def _fetch_fast_minute_bars_with_meta(symbol: str, trade_date: str) -> tuple[list[MinuteBar], str]:
    cached = _read_minute_bars_cache(symbol, trade_date)
    if cached is not None and _minute_bars_cache_is_fresh(symbol, trade_date):
        return cached, _read_minute_bars_cache_source(symbol, trade_date) or "cache"
    try:
        bars = _fetch_sina_minute_bars(symbol, trade_date)
        if bars:
            _write_minute_bars_cache(symbol, trade_date, bars, source="sina")
            return bars, "sina"
    except Exception:
        pass
    try:
        bars = _fetch_fast_trend_bars(symbol, trade_date)
        _write_minute_bars_cache(symbol, trade_date, bars, source="eastmoney-trends")
        return bars, "eastmoney-trends"
    except Exception:
        pass
    code = symbol.split(".", 1)[0]
    secid = f"{_market_id(symbol)}.{code}"
    query = urlencode(
        {
            "secid": secid,
            "klt": "1",
            "fqt": "0",
            "beg": trade_date.replace("-", "") + "093000",
            "end": trade_date.replace("-", "") + "150000",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }
    )
    url = f"{EASTMONEY_KLINE_URL}?{query}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=2.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        completed = subprocess.run(
            [
                "/usr/bin/curl",
                "-L",
                "--compressed",
                "-A",
                headers["User-Agent"],
                "-e",
                headers["Referer"],
                "-m",
                "4",
                "-s",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        raw = completed.stdout.strip()
        if completed.returncode != 0 or not raw:
            raise RuntimeError(f"kline curl failed: {completed.stderr.strip() or completed.returncode}")
        payload = json.loads(raw)
    data = payload.get("data") or {}
    prev_close = float(data.get("preKPrice") or 0)
    bars = []
    for row in data.get("klines") or []:
        parts = str(row).split(",")
        if len(parts) < 7:
            continue
        bars.append(
            MinuteBar(
                ts=parts[0],
                open=float(parts[1] or 0),
                close=float(parts[2] or 0),
                high=float(parts[3] or 0),
                low=float(parts[4] or 0),
                volume=float(parts[5] or 0),
                amount=float(parts[6] or 0),
                prev_close=prev_close,
            )
        )
    _write_minute_bars_cache(symbol, trade_date, bars, source="eastmoney-kline")
    return bars, "eastmoney-kline"


def _fetch_sina_minute_bars(symbol: str, trade_date: str) -> list[MinuteBar]:
    sina_symbol = _sina_symbol(symbol.split(".", 1)[0])
    callback = f"var_{sina_symbol}_1"
    query = urlencode(
        {
            "symbol": sina_symbol,
            "scale": "1",
            "ma": "no",
            "datalen": "512",
        }
    )
    url = f"https://quotes.sina.cn/cn/api/jsonp.php/{callback}=/CN_MarketDataService.getKLineData?{query}"
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/javascript,text/javascript,*/*",
            "Referer": "https://finance.sina.com.cn/",
        },
    )
    with urlopen(request, timeout=6) as response:
        raw = response.read().decode("utf-8", errors="ignore")
    start = raw.find("([")
    end = raw.rfind(")")
    if start < 0 or end < start:
        raise RuntimeError("sina minute response format changed")
    rows = json.loads(raw[start + 1 : end])
    prev_close = 0.0
    bars: list[MinuteBar] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = str(row.get("day") or "")
        if not ts.startswith(trade_date):
            close = _number(row.get("close"))
            if close > 0:
                prev_close = close
            continue
        close = _number(row.get("close"))
        open_price = _number(row.get("open")) or close
        bars.append(
            MinuteBar(
                ts=ts[:16],
                open=open_price,
                close=close,
                high=_number(row.get("high")) or close,
                low=_number(row.get("low")) or close,
                volume=_number(row.get("volume")),
                amount=_number(row.get("amount")),
                prev_close=prev_close,
            )
        )
    if bars and not bars[0].prev_close:
        for bar in bars:
            bar.prev_close = bars[0].open
    return bars


def _sina_symbol(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def _minute_bars_cache_path(symbol: str, trade_date: str) -> Path:
    code, market = symbol.split(".", 1)
    return Path("data/history_cache/bars") / f"{trade_date}_{code}_{market}.json"


def _minute_bars_cache_meta_path(symbol: str, trade_date: str) -> Path:
    code, market = symbol.split(".", 1)
    return Path("data/history_cache/bars") / f"{trade_date}_{code}_{market}.meta.json"


def _read_minute_bars_cache(symbol: str, trade_date: str) -> list[MinuteBar] | None:
    path = _minute_bars_cache_path(symbol, trade_date)
    if not path.exists():
        return None
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        return [
            MinuteBar(
                ts=str(row.get("ts") or ""),
                open=float(row.get("open") or 0),
                close=float(row.get("close") or 0),
                high=float(row.get("high") or 0),
                low=float(row.get("low") or 0),
                volume=float(row.get("volume") or 0),
                amount=float(row.get("amount") or 0),
                prev_close=float(row.get("prev_close") or 0),
            )
            for row in rows
            if isinstance(row, dict)
        ]
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _read_minute_bars_cache_source(symbol: str, trade_date: str) -> str:
    path = _minute_bars_cache_meta_path(symbol, trade_date)
    if not path.exists():
        return "cache"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return str(payload.get("source") or "cache") if isinstance(payload, dict) else "cache"
    except (OSError, json.JSONDecodeError):
        return "cache"


def _minute_bars_cache_is_fresh(symbol: str, trade_date: str) -> bool:
    if trade_date != date.today().isoformat():
        return True
    path = _minute_bars_cache_meta_path(symbol, trade_date)
    max_age = float(os.environ.get("LIMIT_UP_KLINE_CACHE_MAX_AGE_SEC", "6") or 6)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        updated_at = _number(payload.get("updated_at")) if isinstance(payload, dict) else 0
    except (OSError, json.JSONDecodeError):
        updated_at = 0
    return bool(updated_at and time.time() - updated_at <= max_age)


def _write_minute_bars_cache(symbol: str, trade_date: str, bars: list[MinuteBar], source: str = "cache") -> None:
    path = _minute_bars_cache_path(symbol, trade_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "ts": bar.ts,
            "open": bar.open,
            "close": bar.close,
            "high": bar.high,
            "low": bar.low,
            "volume": bar.volume,
            "amount": bar.amount,
            "prev_close": bar.prev_close,
        }
        for bar in bars
    ]
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    _minute_bars_cache_meta_path(symbol, trade_date).write_text(
        json.dumps({"source": source, "updated_at": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )


def _fetch_fast_trend_bars(symbol: str, trade_date: str) -> list[MinuteBar]:
    code = symbol.split(".", 1)[0]
    secid = f"{_market_id(symbol)}.{code}"
    query = urlencode(
        {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "iscr": "0",
            "ndays": "5",
        }
    )
    url = f"{EASTMONEY_TRENDS_URL}?{query}"
    completed = subprocess.run(
        [
            "/usr/bin/curl",
            "-L",
            "--compressed",
            "-A",
            "Mozilla/5.0",
            "-e",
            "https://quote.eastmoney.com/",
            "-m",
            "4",
            "-s",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    raw = completed.stdout.strip()
    if completed.returncode != 0 or not raw:
        raise RuntimeError(f"trend curl failed: {completed.stderr.strip() or completed.returncode}")
    payload = json.loads(raw)
    data = payload.get("data") or {}
    prev_close = float(data.get("preClose") or 0)
    prefix = trade_date.replace("-", "")
    bars = []
    for row in data.get("trends") or []:
        parts = str(row).split(",")
        if len(parts) < 7:
            continue
        ts = parts[0]
        if ts[:10].replace("-", "") != prefix:
            continue
        close = float(parts[2] or 0)
        open_price = float(parts[1] or 0) or close
        bars.append(
            MinuteBar(
                ts=ts,
                open=open_price,
                close=close,
                high=float(parts[3] or close),
                low=float(parts[4] or close),
                volume=float(parts[5] or 0),
                amount=float(parts[6] or 0),
                prev_close=prev_close,
            )
        )
    return bars


def _market_id(symbol: str) -> str:
    return "1" if symbol.endswith(".SH") else "0"


def _analyze_intraday_kline(bars: list[Any]) -> dict[str, Any]:
    if len(bars) < 3:
        return {
            "available": False,
            "signal": "unavailable",
            "score_delta": 0,
            "reasons": [],
            "risks": [],
        }
    first = bars[0]
    last = bars[-1]
    open_price = float(getattr(first, "open", 0) or 0)
    last_close = float(getattr(last, "close", 0) or 0)
    prev_close = float(getattr(first, "prev_close", 0) or 0) or open_price
    lows = [float(getattr(bar, "low", 0) or 0) for bar in bars if getattr(bar, "low", 0)]
    amounts = [float(getattr(bar, "amount", 0) or 0) for bar in bars if getattr(bar, "amount", 0)]
    volumes = [float(getattr(bar, "volume", 0) or 0) for bar in bars if getattr(bar, "volume", 0)]
    score_delta = 0
    reasons: list[str] = []
    risks: list[str] = []
    dimensions = {"pull": 0, "reclaim": 0, "seal": 0, "vwap": 0, "volume": 0}

    total_volume = sum(volumes)
    total_amount = sum(amounts)
    vwap = total_amount / (total_volume * 100) if total_volume > 0 else 0
    if total_volume > 0 and 0 < vwap < 1:
        vwap = total_amount / total_volume
    if open_price and last_close >= open_price:
        score_delta += 10
        dimensions["reclaim"] += 10
        reasons.append("分时站稳开盘价")
    if vwap and last_close >= vwap:
        score_delta += 8
        dimensions["vwap"] += 8
        reasons.append("价格站上分时均价")
    recent_lows = lows[-8:] if len(lows) >= 8 else lows
    if open_price and recent_lows and min(recent_lows) >= open_price * 0.985:
        score_delta += 8
        dimensions["reclaim"] += 8
        reasons.append("回踩开盘不破")
    if len(bars) >= 4:
        base_close = float(getattr(bars[-4], "close", 0) or 0)
        rise_3m_pct = _pct(last_close, base_close) if base_close else 0
    else:
        rise_3m_pct = 0
    avg_amount = total_amount / len(amounts) if amounts else 0
    last3_amount = sum(amounts[-3:]) if amounts else 0
    volume_expanding = bool(avg_amount and last3_amount >= avg_amount * 3.2)
    if rise_3m_pct >= 1.0:
        score_delta += 10
        dimensions["pull"] += 10
        reasons.append("3分钟上攻")
    if volume_expanding:
        score_delta += 8
        dimensions["volume"] += 8
        reasons.append("分时放量")
    if prev_close and last_close >= prev_close * 1.085:
        score_delta += 14
        dimensions["seal"] += 14
        reasons.append("逼近涨停确认")
    if open_price and last_close < open_price * 0.98:
        score_delta -= 24
        dimensions["reclaim"] -= 12
        risks.append("跌破开盘承接")
    if vwap and last_close < vwap * 0.99:
        score_delta -= 12
        dimensions["vwap"] -= 8
        risks.append("跌破分时均价")
    if lows and prev_close and min(lows[-5:]) < prev_close * 0.97:
        score_delta -= 10
        risks.append("盘中回撤偏深")

    signal = "strong" if score_delta >= 24 and not risks else "weak" if score_delta < 0 or risks else "watch"
    return {
        "available": True,
        "signal": signal,
        "score_delta": score_delta,
        "reasons": reasons[:5],
        "risks": risks[:4],
        "last_time": str(getattr(last, "ts", "")),
        "rise_3m_pct": round(rise_3m_pct, 2),
        "vwap": round(vwap, 3) if vwap else 0,
        "bar_count": len(bars),
        "dimensions": dimensions,
    }


def _code_symbol(code: str) -> str:
    text = str(code).strip()
    if text.startswith(("6", "9")):
        return f"{text}.SH"
    if text.startswith(("4", "8")):
        return f"{text}.BJ"
    return f"{text}.SZ"


def _build_next_day_rows(
    watch_pool: list[dict[str, Any]],
    quotes: dict[str, Any],
    today_by_code: dict[str, dict[str, Any]],
    sector_strength: dict[str, dict[str, Any]],
    focus_by_code: dict[str, dict[str, Any]] | None = None,
    kline_by_code: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows = []
    focus_by_code = focus_by_code or {}
    kline_by_code = kline_by_code or {}
    for source in watch_pool:
        code = str(source.get("code") or "")
        focus_item = focus_by_code.get(code) or {}
        quote = quotes.get(code) or {}
        kline = kline_by_code.get(code) or {}
        prev_close = _number(quote.get("prev_close")) or _number(source.get("price"))
        open_price = _number(quote.get("open"))
        price = _number(quote.get("price"))
        high_price = _number(quote.get("high"))
        low_price = _number(quote.get("low"))
        change_pct = _number(quote.get("change_pct"))
        amount = _number(quote.get("amount"))
        open_pct = round((open_price / prev_close - 1) * 100, 2) if prev_close and open_price else 0
        close_from_open_pct = round((price / open_price - 1) * 100, 2) if open_price and price else 0
        high_from_open_pct = round((high_price / open_price - 1) * 100, 2) if open_price and high_price else 0
        low_from_open_pct = round((low_price / open_price - 1) * 100, 2) if open_price and low_price else 0
        sealed = code in today_by_code
        today_item = today_by_code.get(code, {})
        today_first_time = today_item.get("first_limit_time")
        today_last_time = today_item.get("last_limit_time")
        today_open_count = int(today_item.get("open_board_count") or 0)
        sector = str(source.get("sector") or quote.get("sector") or "未分组")
        theme = sector_strength.get(sector) or {}
        sector_trend = _sector_trend(theme)
        score = 0
        reasons = []
        if sealed:
            score += 45
            reasons.append("今日已封板")
        if open_pct >= 2:
            score += 18
            reasons.append("高开承接")
        if change_pct >= 5:
            score += 18
            reasons.append("快速走强")
        if open_price and price >= open_price:
            score += 10
            reasons.append("站上开盘价")
        if amount >= 50_000_000:
            score += 12
            reasons.append("成交额放大")
        if int(theme.get("limit_count") or 0) >= 3:
            score += 15
            reasons.append(f"{sector}板块确认")
        if sector_trend == "enhancing":
            score += 8
            reasons.append("板块动态增强")
        elif sector_trend == "fading":
            score -= 8
            reasons.append("板块退潮")
        tier = str(focus_item.get("openclaw_tier") or "")
        if tier == "core":
            score += 10
            reasons.append("OpenClaw核心")
        elif tier == "watch":
            score += 4
            reasons.append("OpenClaw观察")
        elif tier == "avoid":
            score -= 35
            reasons.append("OpenClaw剔除")
        if open_pct <= -3:
            score -= 25
            reasons.append("低开弱于预期")
        if open_price and price < open_price * 0.98:
            score -= 18
            reasons.append("跌破开盘承接")
        if kline.get("available"):
            score += _number(kline.get("score_delta"))
            reasons.extend(_string_list(kline.get("reasons")))
            reasons.extend(_string_list(kline.get("risks")))
        intraday_status = "normal"
        if tier in {"core", "watch"} and (kline.get("signal") == "weak" or (change_pct < 2 and not sealed)):
            score -= 18
            intraday_status = "downgraded"
            reasons.append("OpenClaw盘中降级")

        tradability = _tradability_state(
            sealed=sealed,
            first_time=today_first_time,
            open_board_count=today_open_count,
            open_price=open_price,
            low_price=low_price,
            prev_close=prev_close,
            seal_amount=_number(today_item.get("seal_amount")),
        )
        buy_unavailable = tradability["status"] == "unavailable"
        action = "BUY" if sealed or score >= 58 else "WATCH" if score >= 34 else "PASS"
        if intraday_status == "downgraded" and action == "BUY" and not sealed:
            action = "WATCH"
        if buy_unavailable and action == "BUY":
            action = "WATCH"
        if kline.get("signal") == "weak" and action == "BUY" and not sealed:
            action = "WATCH"
        if tier == "avoid" and action == "BUY":
            action = "WATCH"
        state = _next_day_state(
            action=action,
            sealed=sealed,
            buy_unavailable=buy_unavailable,
            change_pct=change_pct,
            kline_signal=str(kline.get("signal") or ""),
            today_open_count=today_open_count,
            today_first_time=today_first_time,
            today_last_time=today_last_time,
        )
        if tier == "avoid" and action == "WATCH":
            state = "风险观察"
        rows.append(
            {
                "code": code,
                "name": source.get("name") or quote.get("name") or code,
                "sector": sector,
                "source_streak": int(source.get("streak") or 1),
                "source_first_limit_time": source.get("first_limit_time"),
                "price": round(price, 3),
                "open": round(open_price, 3),
                "high": round(high_price, 3),
                "low": round(low_price, 3),
                "prev_close": round(prev_close, 3),
                "open_pct": open_pct,
                "close_from_open_pct": close_from_open_pct,
                "high_from_open_pct": high_from_open_pct,
                "low_from_open_pct": low_from_open_pct,
                "change_pct": round(change_pct, 2),
                "amount": amount,
                "sealed_today": sealed,
                "today_first_limit_time": today_first_time,
                "today_last_limit_time": today_last_time,
                "today_open_board_count": today_open_count,
                "buy_stage": state,
                "buy_unavailable": buy_unavailable,
                "signal_stage": "official" if sealed else "trial" if action == "BUY" else "watch",
                "tradability": tradability["status"],
                "trade_hint": tradability["hint"],
                "openclaw_tier": tier or "rule",
                "openclaw_score": focus_item.get("openclaw_score"),
                "openclaw_summary": focus_item.get("openclaw_summary"),
                "openclaw_intraday_status": intraday_status,
                "action": action,
                "state": state,
                "score": round(score, 1),
                "reasons": reasons[:8],
                "kline_signal": kline.get("signal") or "unavailable",
                "kline_source": kline.get("source") or "unavailable",
                "kline_reasons": _string_list(kline.get("reasons")),
                "kline_risks": _string_list(kline.get("risks")),
                "kline_last_time": kline.get("last_time") or "",
                "kline_rise_3m_pct": kline.get("rise_3m_pct", 0),
                "kline_vwap": kline.get("vwap", 0),
                "kline_dimensions": kline.get("dimensions") or {},
                "sector_trend": sector_trend,
                "risk_note": "只在强承接/封板确认时参与，低开或跌破开盘价放弃。",
            }
        )
    return sorted(rows, key=lambda item: (item["action"] == "BUY", item["score"], item["amount"]), reverse=True)


def _sector_trend(theme: dict[str, Any]) -> str:
    limit_count = int(theme.get("limit_count") or 0)
    early_count = int(theme.get("early_count") or 0)
    max_streak = int(theme.get("max_streak") or 0)
    if limit_count >= 5 or (limit_count >= 3 and early_count >= 2) or max_streak >= 3:
        return "enhancing"
    if limit_count <= 1:
        return "fading"
    return "normal"


def _is_buy_unavailable(first_time: Any, open_board_count: int, open_price: float = 0, low_price: float = 0, prev_close: float = 0, seal_amount: float = 0) -> bool:
    return _tradability_state(
        sealed=True,
        first_time=first_time,
        open_board_count=open_board_count,
        open_price=open_price,
        low_price=low_price,
        prev_close=prev_close,
        seal_amount=seal_amount,
    )["status"] == "unavailable"


def _tradability_state(
    *,
    sealed: bool,
    first_time: Any,
    open_board_count: int,
    open_price: float = 0,
    low_price: float = 0,
    prev_close: float = 0,
    seal_amount: float = 0,
) -> dict[str, str]:
    if not sealed:
        return {"status": "tradable", "hint": "未封板试探，跌破开盘价放弃"}
    first_sort = _time_sort(first_time)
    limit_price = prev_close * 1.1 if prev_close else 0
    one_line = bool(limit_price and open_price >= limit_price * 0.995 and low_price >= limit_price * 0.995 and open_board_count <= 0)
    early_sealed = bool(first_sort <= 930 and open_board_count <= 0)
    huge_seal = bool(seal_amount >= 300_000_000 and open_board_count <= 0 and first_sort <= 935)
    if one_line or early_sealed or huge_seal:
        return {"status": "unavailable", "hint": "封死/一字特征，买不到不追"}
    if first_sort <= 935 and open_board_count <= 0:
        return {"status": "queue", "hint": "早盘强封，能排则排，买不到放弃"}
    if open_board_count > 0:
        return {"status": "tradable", "hint": "回封确认，仍需确认可成交"}
    return {"status": "queue", "hint": "封板确认，排队优先"}


def _official_candidate_sort_key(item: dict[str, Any]) -> tuple[int, int, int, float, float, float]:
    tier = str(item.get("openclaw_tier") or "")
    tier_score = {"core": 4, "watch": 3, "rule": 2, "": 2}.get(tier, 1)
    stage_score = 3 if item.get("sealed_today") else 2 if item.get("kline_signal") == "strong" else 1
    state = str(item.get("state") or "")
    state_score = 3 if state in {"首封确认", "回封确认"} else 2 if state in {"分时确认", "开盘确认"} else 1
    return (
        tier_score,
        stage_score,
        state_score,
        _number(item.get("score")),
        _number(item.get("amount")),
        -_time_sort(item.get("today_first_limit_time") or "99:99"),
    )


def _official_candidate_allowed(item: dict[str, Any], session: dict[str, Any] | None = None) -> bool:
    if item.get("buy_unavailable"):
        return False
    return True


def _should_release_official_lock(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    execution = str(row.get("execution_status") or "")
    if execution == "filled":
        return False
    if execution in {"missed", "abandoned"}:
        return True
    state = str(row.get("state") or "")
    if row.get("buy_unavailable") or state in {"买不到", "放弃", "风险观察"}:
        return True
    if row.get("kline_signal") == "weak" and not row.get("sealed_today"):
        return True
    if _number(row.get("close_from_open_pct")) <= -2.5 and not row.get("sealed_today"):
        return True
    if _number(row.get("score")) < 34 and not row.get("sealed_today"):
        return True
    return False


def _entry_price(item: dict[str, Any]) -> float:
    price = _number(item.get("price"))
    prev_close = _number(item.get("prev_close"))
    state = str(item.get("state") or "")
    if state in {"首封确认", "回封确认"} and prev_close:
        return round(prev_close * 1.1, 3)
    return round(price, 3)


def _official_reason(item: dict[str, Any]) -> str:
    reasons = _string_list(item.get("reasons"))
    return "；".join(reasons[:3]) or str(item.get("state") or "正式买点")


def _next_day_state(
    *,
    action: str,
    sealed: bool,
    buy_unavailable: bool,
    change_pct: float,
    kline_signal: str,
    today_open_count: int,
    today_first_time: Any,
    today_last_time: Any,
) -> str:
    if buy_unavailable:
        return "买不到"
    if sealed:
        if today_open_count > 0 or (today_first_time and today_last_time and str(today_first_time) != str(today_last_time)):
            return "回封确认"
        return "首封确认"
    if action == "BUY" and kline_signal == "strong":
        return "分时确认"
    if change_pct >= 8.5 and action != "PASS":
        return "冲板临界"
    if action == "BUY":
        return "开盘确认"
    if action == "WATCH":
        return "观察承接"
    return "放弃"


def _latest_trading_date(value: str = "") -> str:
    current = date.fromisoformat(value) if value else date.today()
    for _ in range(30):
        if is_trading_date(current):
            return current.strftime("%Y-%m-%d")
        current -= timedelta(days=1)
    return current.strftime("%Y-%m-%d")


def _focus_review_date_for_session(session: dict[str, Any]) -> str:
    session_date = str(session.get("date") or "")
    current_date = _latest_trading_date(session_date)
    if session_date != current_date:
        return current_date
    phase = str(session.get("code") or "")
    if phase in {"POST_CLOSE", "CLOSED"}:
        return current_date
    return _previous_trading_date(current_date)


def _previous_trading_date(value: str) -> str:
    current = date.fromisoformat(value) - timedelta(days=1)
    for _ in range(30):
        if is_trading_date(current):
            return current.strftime("%Y-%m-%d")
        current -= timedelta(days=1)
    return current.strftime("%Y-%m-%d")


def _next_trading_date(value: str) -> str:
    current = date.fromisoformat(value) + timedelta(days=1)
    for _ in range(30):
        if is_trading_date(current):
            return current.strftime("%Y-%m-%d")
        current += timedelta(days=1)
    return current.strftime("%Y-%m-%d")


def _time_label(value: Any) -> str:
    text = str(int(_number(value))).zfill(6)
    if text == "000000":
        return "--"
    return f"{text[:2]}:{text[2:4]}"


def _time_sort(value: Any) -> int:
    text = str(value or "99:99").replace(":", "")
    try:
        return int(text)
    except ValueError:
        return 9999


def _session_hhmm(session: dict[str, Any] | None) -> int:
    text = str((session or {}).get("time") or "")
    if not text:
        return 9999
    parts = text.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        return hour * 100 + minute
    except (IndexError, ValueError):
        return 9999


def _price(value: Any) -> float:
    return round(_number(value) / 1000, 3)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _pct(price: float, base: float) -> float:
    if not base:
        return 0.0
    return (price / base - 1) * 100
