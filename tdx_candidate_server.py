from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from pathlib import Path
from urllib.parse import urlparse

from market_data import Tick
from sectors import load_sectors

try:
    from opentdx.const import CATEGORY, MARKET, SORT_TYPE
    from opentdx.tdxClient import TdxClient
except ImportError as error:  # pragma: no cover - depends on optional runtime
    raise RuntimeError("需要安装 opentdx：python3 -m venv .venv-tdx && .venv-tdx/bin/pip install opentdx") from error


HOST = os.environ.get("TDX_HOST", "127.0.0.1")
PORT = int(os.environ.get("TDX_PORT", "9002"))
INTERVAL = float(os.environ.get("TDX_INTERVAL", "3"))
SCAN_COUNT = int(os.environ.get("TDX_SCAN_COUNT", "200"))
OUTPUT_COUNT = int(os.environ.get("TDX_OUTPUT_COUNT", "80"))
TOP_SYMBOL_COUNT = int(os.environ.get("TDX_TOP_SYMBOL_COUNT", "5"))
TOP_REFRESH_INTERVAL = float(os.environ.get("TDX_TOP_REFRESH_INTERVAL", "30"))
TOP_MIN_HOLD_SEC = float(os.environ.get("TDX_TOP_MIN_HOLD_SEC", "180"))
TOP_REPLACE_RATIO = float(os.environ.get("TDX_TOP_REPLACE_RATIO", "1.2"))
TOP_ALERT_HOLD_SEC = float(os.environ.get("TDX_TOP_ALERT_HOLD_SEC", "600"))
TOP_COOLDOWN_SEC = float(os.environ.get("TDX_TOP_COOLDOWN_SEC", "120"))
TOP_SYMBOLS_PATH = Path(os.environ.get("TDX_TOP_SYMBOLS_PATH", "data/tdx_top_symbols.json"))
RECENT_ALERTS_PATH = Path(os.environ.get("TDX_RECENT_ALERTS_PATH", "data/recent_alerts.json"))
SYMBOLS_CACHE = Path(os.environ.get("TICKDB_SYMBOLS_CACHE", "data/tickdb_cn_symbols.json"))
SECTOR_CACHE = Path(os.environ.get("TDX_SECTOR_CACHE", "data/tdx_stock_sectors.json"))
EXCLUDE_ST = os.environ.get("TDX_EXCLUDE_ST", "1") == "1"
EXCLUDE_NEW = os.environ.get("TDX_EXCLUDE_NEW", "1") == "1"
EXCLUDE_BJ = os.environ.get("TDX_EXCLUDE_BJ", "1") == "1"
EXCLUDE_GEM = os.environ.get("TDX_EXCLUDE_GEM", "1") == "1"
EXCLUDE_STAR = os.environ.get("TDX_EXCLUDE_STAR", "1") == "1"
GEM_MAX_CHANGE_PCT = float(os.environ.get("TDX_GEM_MAX_CHANGE_PCT", "9.5"))
GEM_MAX_RISE_SPEED_PCT = float(os.environ.get("TDX_GEM_MAX_RISE_SPEED_PCT", "3.0"))
GEM_MAX_TURNOVER_RATE = float(os.environ.get("TDX_GEM_MAX_TURNOVER_RATE", "18"))
GEM_MIN_MIN2_AMOUNT = float(os.environ.get("TDX_GEM_MIN_MIN2_AMOUNT", "8000000"))
MAIN_MAX_CHANGE_PCT = float(os.environ.get("TDX_MAIN_MAX_CHANGE_PCT", "10.3"))
MAIN_MAX_TURNOVER_RATE = float(os.environ.get("TDX_MAIN_MAX_TURNOVER_RATE", "16"))
MAIN_MIN_MIN2_AMOUNT = float(os.environ.get("TDX_MAIN_MIN_MIN2_AMOUNT", "6000000"))
MAIN_MIN_ACTIVE_BUY_RATIO = float(os.environ.get("TDX_MAIN_MIN_ACTIVE_BUY_RATIO", "0.42"))
MAIN_MAX_RISE_SPEED_PCT = float(os.environ.get("TDX_MAIN_MAX_RISE_SPEED_PCT", "4.8"))
MAIN_MIN_CHANGE_PCT = float(os.environ.get("TDX_MAIN_MIN_CHANGE_PCT", "0.5"))


class TdxCandidateFeed:
    def __init__(self) -> None:
        self.latest: list[dict] = []
        self.candidates: list[dict] = []
        self.last_error = ""
        self.batch_count = 0
        self.last_batch_ts = 0.0
        self.last_top_write_ts = 0.0
        self.last_top_symbols: list[str] = []
        self.active_top: dict[str, dict] = {}
        self.cooldown: dict[str, float] = {}
        self.filtered_count = 0
        self.filtered_reasons: dict[str, int] = {}
        self.sector_heat: list[dict] = []
        self.code_to_sector = self._build_sector_index()
        self.symbol_meta = self._load_symbol_meta()
        self.tdx_sector_cache = self._load_tdx_sector_cache()
        self.last_sector_cache_write_ts = 0.0

    async def start(self) -> None:
        while True:
            try:
                ticks, candidates = await asyncio.to_thread(self._fetch_candidates)
                self.latest = [tick.__dict__ for tick in ticks]
                self.candidates = candidates
                self.batch_count += 1
                self.last_batch_ts = time.time()
                self.last_error = ""
                self._write_top_symbols(candidates)
                self._mark_active_top(candidates)
            except Exception as error:
                self.last_error = f"{error.__class__.__name__}: {error}"
            await asyncio.sleep(INTERVAL)

    def health(self) -> dict:
        return {
            "source": "opentdx",
            "scan_count": SCAN_COUNT,
            "output_count": OUTPUT_COUNT,
            "top_symbol_count": TOP_SYMBOL_COUNT,
            "top_refresh_interval": TOP_REFRESH_INTERVAL,
            "top_min_hold_sec": TOP_MIN_HOLD_SEC,
            "top_replace_ratio": TOP_REPLACE_RATIO,
            "top_alert_hold_sec": TOP_ALERT_HOLD_SEC,
            "top_cooldown_sec": TOP_COOLDOWN_SEC,
            "active_top": list(self.active_top.values()),
            "cooldown_count": len(self.cooldown),
            "batch_count": self.batch_count,
            "last_batch_ts": self.last_batch_ts,
            "last_error": self.last_error,
            "tick_count": len(self.latest),
            "filtered_count": self.filtered_count,
            "filtered_reasons": self.filtered_reasons,
            "sector_heat": self.sector_heat,
            "top_symbols_path": str(TOP_SYMBOLS_PATH),
        }

    def _fetch_candidates(self) -> tuple[list[Tick], list[dict]]:
        rows = []
        with TdxClient() as client:
            speed_rows = client.stock_quotes_list(CATEGORY.A, count=SCAN_COUNT, sort_type=SORT_TYPE.SPEED_PCT, reverse=False)
            amount_rows = client.stock_quotes_list(CATEGORY.A, count=SCAN_COUNT, sort_type=SORT_TYPE.AMOUNT_2M, reverse=False)
            rows = self._merge_rows(speed_rows, amount_rows)

            raw_candidates = [self._candidate(row) for row in rows]
            candidates = []
            filtered_reasons: dict[str, int] = {}
            for item in raw_candidates:
                reason = self._filter_reason(item)
                if reason:
                    filtered_reasons[reason] = filtered_reasons.get(reason, 0) + 1
                    continue
                candidates.append(item)
            self.filtered_count = sum(filtered_reasons.values())
            self.filtered_reasons = filtered_reasons
            candidates.sort(key=self._score_candidate, reverse=True)
            candidates = candidates[:OUTPUT_COUNT]
            self._enrich_sectors(candidates, client)
            for item in candidates:
                item["candidate_score"] = round(self._score_candidate(item), 2)
                item["candidate_reasons"] = self._candidate_reasons(item)
            self._mark_active_top(candidates)
            self.sector_heat = self._sector_heat(candidates)
        return [self._to_tick(item) for item in candidates], candidates

    def _merge_rows(self, *row_groups: list[dict]) -> list[dict]:
        merged: dict[str, dict] = {}
        for rows in row_groups:
            for row in rows:
                code = str(row.get("code", "")).strip()
                if code:
                    merged[code] = row
        return list(merged.values())

    def _candidate(self, row: dict) -> dict:
        code = str(row.get("code", "")).strip()
        symbol = self._symbol(row, code)
        price = float(row.get("close") or 0)
        prev_close = float(row.get("pre_close") or price or 0)
        auction_price = _first_float(row, ("auction_price", "call_auction_price", "open", "open_price"))
        auction_amount = _first_float(row, ("auction_amount", "call_auction_amount", "open_amount", "preopen_amount"))
        if 0 < auction_amount < 1_000_000:
            auction_amount *= 10_000
        auction_volume_ratio = _first_float(row, ("auction_volume_ratio", "call_auction_volume_ratio", "open_volume_ratio"))
        volume = int(float(row.get("vol") or 0))
        amount = float(row.get("amount") or 0)
        in_vol = float(row.get("in_vol") or 0)
        out_vol = float(row.get("out_vol") or 0)
        bid_amount, ask_amount = self._depth_amount(row.get("handicap") or {}, price)
        return {
            "symbol": symbol,
            "code": code,
            "name": self._name(symbol, code),
            "sector": self._cached_sector(symbol, code),
            "board": self._infer_board(code),
            "price": price,
            "prev_close": prev_close,
            "auction_price": auction_price,
            "auction_change_pct": ((auction_price / prev_close) - 1) * 100 if auction_price > 0 and prev_close > 0 else None,
            "auction_amount": auction_amount,
            "auction_volume_ratio": auction_volume_ratio,
            "auction_source": "tdx" if auction_price > 0 else "",
            "change_pct": ((price / prev_close) - 1) * 100 if prev_close > 0 else 0.0,
            "volume": volume,
            "turnover": amount,
            "active_buy_ratio": in_vol / max(in_vol + out_vol, 1),
            "bid_amount": bid_amount,
            "ask_amount": ask_amount,
            "rise_speed_pct": _pct(row.get("rise_speed")),
            "short_turnover_pct": _pct(row.get("short_turnover")),
            "vol_rise_speed_pct": _pct(row.get("vol_rise_speed")),
            "min2_amount": float(row.get("min2_amount") or 0),
            "turnover_rate": float(row.get("turnover") or 0),
            "server_time": str(row.get("server_time", "")),
        }

    def _filter_reason(self, item: dict) -> str:
        name = item["name"].upper()
        code = item["code"]
        if item["price"] <= 0 or item["prev_close"] <= 0:
            return "bad_price"
        if EXCLUDE_ST and ("ST" in name or code.startswith("退")):
            return "st"
        if EXCLUDE_NEW and self._is_new_stock_name(item["name"]):
            return "new_stock"
        if EXCLUDE_BJ and item["board"] == "bj":
            return "bj"
        if EXCLUDE_GEM and item["board"] == "gem":
            return "gem"
        if EXCLUDE_STAR and item["board"] == "star":
            return "star"
        if item["board"] == "gem":
            if abs(item["change_pct"]) >= GEM_MAX_CHANGE_PCT:
                return "gem_change"
            if abs(item["rise_speed_pct"]) >= GEM_MAX_RISE_SPEED_PCT and item["min2_amount"] < GEM_MIN_MIN2_AMOUNT:
                return "gem_speed_noise"
            if item["turnover_rate"] >= GEM_MAX_TURNOVER_RATE:
                return "gem_turnover"
        if item["board"] == "main":
            if item["change_pct"] < MAIN_MIN_CHANGE_PCT:
                return "main_weak_change"
            if item["change_pct"] >= MAIN_MAX_CHANGE_PCT:
                return "main_high"
            if item["turnover_rate"] >= MAIN_MAX_TURNOVER_RATE:
                return "main_turnover"
            if item["min2_amount"] < MAIN_MIN_MIN2_AMOUNT and item["rise_speed_pct"] >= 1.2:
                return "main_amount"
            if item["active_buy_ratio"] < MAIN_MIN_ACTIVE_BUY_RATIO and item["rise_speed_pct"] >= 1.0:
                return "main_buy_weak"
            if item["rise_speed_pct"] >= MAIN_MAX_RISE_SPEED_PCT:
                return "main_spike"
        return ""

    def _is_new_stock_name(self, name: str) -> bool:
        normalized = name.strip().upper()
        return normalized.startswith(("N", "C", "U", "W")) or normalized.startswith(("新股", "新债"))

    def _to_tick(self, item: dict) -> Tick:
        return Tick(
            code=item["code"],
            name=item["name"],
            sector=item["sector"],
            board=item["board"],
            ts=time.time(),
            price=round(item["price"], 3),
            prev_close=round(item["prev_close"], 3),
            volume=item["volume"],
            turnover=round(item["turnover"], 2),
            active_buy_ratio=round(item["active_buy_ratio"], 3),
            bid_amount=round(item["bid_amount"], 2),
            ask_amount=round(item["ask_amount"], 2),
        )

    def _score_candidate(self, item: dict) -> float:
        speed = item["rise_speed_pct"]
        amount_score = min(item["min2_amount"] / 1_000_000, 28)
        buy_score = max(item["active_buy_ratio"] - MAIN_MIN_ACTIVE_BUY_RATIO, 0) * 45
        fresh_bonus = self._fresh_start_bonus(item)
        high_penalty = max(item["change_pct"] - 5.5, 0) * 8
        turnover_penalty = max(item["turnover_rate"] - 10, 0) * 1.5
        return (
            min(speed, 3.2) * 30
            + amount_score
            + item["vol_rise_speed_pct"] * 1.5
            + item["short_turnover_pct"]
            + buy_score
            + fresh_bonus
            - high_penalty
            - turnover_penalty
        )

    def _fresh_start_bonus(self, item: dict) -> float:
        bonus = 0.0
        if 0.7 <= item["rise_speed_pct"] <= 2.8:
            bonus += 14
        if 1.5 <= item["change_pct"] <= 5.8:
            bonus += 12
        if item["min2_amount"] >= 10_000_000:
            bonus += 8
        return bonus

    def _candidate_reasons(self, item: dict) -> list[str]:
        reasons = []
        if 0.7 <= item["rise_speed_pct"] <= 2.8:
            reasons.append("刚启动")
        if item["min2_amount"] >= 10_000_000:
            reasons.append("2分钟放量")
        if item["active_buy_ratio"] >= 0.5:
            reasons.append("主买占优")
        if 1.5 <= item["change_pct"] <= 5.8:
            reasons.append("涨幅适中")
        if item["turnover_rate"] <= 8:
            reasons.append("换手可控")
        return reasons[:4]

    def _write_top_symbols(self, candidates: list[dict]) -> None:
        TOP_SYMBOLS_PATH.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        if self.last_top_symbols and now - self.last_top_write_ts < TOP_REFRESH_INTERVAL:
            return
        active_top = self._select_active_top(candidates)
        top = [
            {
                "symbol": item["symbol"],
                "code": item["code"],
                "name": item["name"],
                "score": round(self._score_candidate(item), 2),
                "reasons": self._candidate_reasons(item),
                "rise_speed_pct": item["rise_speed_pct"],
                "min2_amount": item["min2_amount"],
                "server_time": item["server_time"],
                "entered_at": self.active_top[item["symbol"]]["entered_at"],
                "reason": self.active_top[item["symbol"]].get("reason", "score"),
                "age_sec": round(now - self.active_top[item["symbol"]]["entered_at"], 1),
            }
            for item in active_top
            if item["symbol"]
        ]
        symbols = [item["symbol"] for item in top]
        if TOP_SYMBOLS_PATH.exists() and symbols == self.last_top_symbols:
            return
        for symbol in list(self.active_top):
            if symbol not in symbols:
                self.active_top.pop(symbol, None)
        self.last_top_symbols = symbols
        self.last_top_write_ts = now
        TOP_SYMBOLS_PATH.write_text(json.dumps({"updated_at": now, "symbols": top}, ensure_ascii=False, indent=2), encoding="utf-8")

    def _select_active_top(self, candidates: list[dict]) -> list[dict]:
        now = time.time()
        self._prune_cooldown(now)
        alerts = self._recent_alert_codes(now)
        by_symbol = {item["symbol"]: item for item in candidates if item["symbol"]}
        active_symbols = [symbol for symbol in self.last_top_symbols if symbol in self.active_top]

        for symbol, active in list(self.active_top.items()):
            code = str(active.get("code", symbol.split(".", 1)[0]))
            if code in alerts and symbol in by_symbol and symbol not in active_symbols:
                active_symbols.append(symbol)
                active["reason"] = "alert"

        for symbol in list(active_symbols):
            active = self.active_top[symbol]
            code = str(active.get("code", symbol.split(".", 1)[0]))
            if code in alerts and now - float(active.get("entered_at", now)) < TOP_ALERT_HOLD_SEC:
                active["reason"] = "alert"
                continue
            if symbol not in by_symbol and now - active["entered_at"] >= TOP_MIN_HOLD_SEC:
                active_symbols.remove(symbol)
                self.active_top.pop(symbol, None)
                self.cooldown[symbol] = now + TOP_COOLDOWN_SEC

        selected = [by_symbol[symbol] for symbol in active_symbols if symbol in by_symbol]
        selected_symbols = {item["symbol"] for item in selected}

        for item in candidates:
            symbol = item["symbol"]
            if not symbol or symbol in selected_symbols:
                continue
            if symbol in self.cooldown:
                continue
            score = self._score_candidate(item)
            reason = "alert" if item["code"] in alerts else "score"
            if len(selected) < TOP_SYMBOL_COUNT:
                self.active_top[symbol] = {"symbol": symbol, "code": item["code"], "entered_at": now, "score": score, "reason": reason}
                selected.append(item)
                selected_symbols.add(symbol)
                continue

            replace_index = self._replaceable_index(selected, now, score, reason)
            if replace_index is None:
                continue
            removed = selected[replace_index]
            self.active_top.pop(removed["symbol"], None)
            self.cooldown[removed["symbol"]] = now + TOP_COOLDOWN_SEC
            self.active_top[symbol] = {"symbol": symbol, "code": item["code"], "entered_at": now, "score": score, "reason": reason}
            selected[replace_index] = item
            selected_symbols.discard(removed["symbol"])
            selected_symbols.add(symbol)

        selected.sort(key=self._score_candidate, reverse=True)
        return selected[:TOP_SYMBOL_COUNT]

    def _replaceable_index(self, selected: list[dict], now: float, challenger_score: float, challenger_reason: str) -> int | None:
        weakest_index = None
        weakest_score = float("inf")
        for index, item in enumerate(selected):
            active = self.active_top.get(item["symbol"], {})
            age = now - float(active.get("entered_at", now))
            score = self._score_candidate(item)
            if active.get("reason") == "alert" and age < TOP_ALERT_HOLD_SEC:
                continue
            if age < TOP_MIN_HOLD_SEC:
                continue
            if challenger_reason != "alert" and challenger_score < score * TOP_REPLACE_RATIO:
                continue
            if score < weakest_score:
                weakest_score = score
                weakest_index = index
        return weakest_index

    def _mark_active_top(self, candidates: list[dict]) -> None:
        active_symbols = set(self.active_top)
        now = time.time()
        for item in candidates:
            active = self.active_top.get(item["symbol"])
            item["top_status"] = "active" if item["symbol"] in active_symbols else "cooldown" if item["symbol"] in self.cooldown else "candidate"
            if active:
                item["top_age_sec"] = round(now - float(active.get("entered_at", now)), 1)
                item["top_reason"] = active.get("reason", "score")
            elif item["symbol"] in self.cooldown:
                item["cooldown_sec"] = round(max(0, self.cooldown[item["symbol"]] - now), 1)

    def _sector_heat(self, candidates: list[dict]) -> list[dict]:
        groups: dict[str, dict] = {}
        for item in candidates:
            sector = item.get("sector") or "未分组"
            score = float(item.get("candidate_score", self._score_candidate(item)))
            group = groups.setdefault(
                sector,
                {"sector": sector, "count": 0, "score_sum": 0.0, "max_score": 0.0, "min2_amount": 0.0, "active_top": 0},
            )
            group["count"] += 1
            group["score_sum"] += score
            group["max_score"] = max(group["max_score"], score)
            group["min2_amount"] += float(item.get("min2_amount", 0))
            if item.get("top_status") == "active":
                group["active_top"] += 1
        rows = []
        for group in groups.values():
            count = max(group["count"], 1)
            group["avg_score"] = round(group.pop("score_sum") / count, 1)
            group["max_score"] = round(group["max_score"], 1)
            group["min2_amount"] = round(group["min2_amount"], 2)
            rows.append(group)
        rows.sort(key=lambda item: (item["active_top"], item["count"], item["max_score"], item["min2_amount"]), reverse=True)
        return rows[:12]

    def _recent_alert_codes(self, now: float) -> set[str]:
        if not RECENT_ALERTS_PATH.exists():
            return set()
        try:
            payload = json.loads(RECENT_ALERTS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        keep_sec = float(payload.get("keep_sec", TOP_ALERT_HOLD_SEC))
        codes = set()
        for item in payload.get("alerts", []):
            ts = float(item.get("ts", 0))
            if now - ts <= min(keep_sec, TOP_ALERT_HOLD_SEC):
                codes.add(str(item.get("code", "")))
        return codes

    def _prune_cooldown(self, now: float) -> None:
        for symbol, expire_at in list(self.cooldown.items()):
            if expire_at <= now:
                self.cooldown.pop(symbol, None)

    def _depth_amount(self, handicap: dict, price: float) -> tuple[float, float]:
        bid_amount = sum(float(item.get("price") or price) * float(item.get("vol") or 0) * 100 for item in handicap.get("bid", []))
        ask_amount = sum(float(item.get("price") or price) * float(item.get("vol") or 0) * 100 for item in handicap.get("ask", []))
        return bid_amount, ask_amount

    def _symbol(self, row: dict, code: str) -> str:
        market = str(row.get("market", ""))
        if "SH" in market:
            return f"{code}.SH"
        if "SZ" in market:
            return f"{code}.SZ"
        if "BJ" in market:
            return f"{code}.BJ"
        if code.startswith("6"):
            return f"{code}.SH"
        if code.startswith(("0", "2", "3")):
            return f"{code}.SZ"
        if code.startswith(("4", "8", "9")):
            return f"{code}.BJ"
        return code

    def _name(self, symbol: str, code: str) -> str:
        meta = self.symbol_meta.get(symbol, {})
        return str(meta.get("name") or code)

    def _cached_sector(self, symbol: str, code: str) -> str:
        configured = self.code_to_sector.get(code)
        if configured:
            return configured
        return self.tdx_sector_cache.get(symbol, "未分组")

    def _enrich_sectors(self, candidates: list[dict], client: TdxClient) -> None:
        changed = False
        for item in candidates:
            if item["sector"] != "未分组":
                continue
            sector = self._tdx_sector(item, client)
            if sector:
                item["sector"] = sector
                self.tdx_sector_cache[item["symbol"]] = sector
                changed = True
        if changed and time.time() - self.last_sector_cache_write_ts > 10:
            self._write_tdx_sector_cache()

    def _tdx_sector(self, item: dict, client: TdxClient) -> str:
        market = self._market_enum(item["symbol"])
        if market is None:
            return ""
        try:
            payload = client.stock_belong_board(market, item["code"])
        except Exception:
            return ""
        boards = payload.get("data", []) if isinstance(payload, dict) else []
        return self._pick_board_name(boards)

    def _pick_board_name(self, boards: list[dict]) -> str:
        preferred = [board for board in boards if str(board.get("board_type")) == "12"]
        similar = [board for board in preferred if str(board.get("最相似")) == "1"]
        concept = [board for board in boards if str(board.get("board_type")) == "4"]
        for group in (similar, preferred, concept):
            for board in group:
                name = str(board.get("board_symbol_name", "")).strip()
                if name:
                    return name
        return ""

    def _market_enum(self, symbol: str) -> MARKET | None:
        if symbol.endswith(".SH"):
            return MARKET.SH
        if symbol.endswith(".SZ"):
            return MARKET.SZ
        if symbol.endswith(".BJ"):
            return MARKET.BJ
        return None

    def _load_tdx_sector_cache(self) -> dict[str, str]:
        if not SECTOR_CACHE.exists():
            return {}
        try:
            payload = json.loads(SECTOR_CACHE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        sectors = payload.get("sectors", {})
        return sectors if isinstance(sectors, dict) else {}

    def _write_tdx_sector_cache(self) -> None:
        SECTOR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        self.last_sector_cache_write_ts = time.time()
        SECTOR_CACHE.write_text(
            json.dumps({"updated_at": self.last_sector_cache_write_ts, "sectors": self.tdx_sector_cache}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _infer_board(self, code: str) -> str:
        if code.startswith("30"):
            return "gem"
        if code.startswith("68"):
            return "star"
        if code.startswith(("4", "8", "9")):
            return "bj"
        return "main"

    def _build_sector_index(self) -> dict[str, str]:
        index = {}
        for sector, codes in load_sectors().items():
            for code in codes:
                index[str(code)] = sector
        return index

    def _load_symbol_meta(self) -> dict[str, dict]:
        if not SYMBOLS_CACHE.exists():
            return {}
        payload = json.loads(SYMBOLS_CACHE.read_text(encoding="utf-8"))
        meta = payload.get("meta", {})
        return meta if isinstance(meta, dict) else {}


FEED = TdxCandidateFeed()


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        writer.close()
        await writer.wait_closed()
        return

    request_line = request.split(b"\r\n", 1)[0].decode("utf-8", errors="ignore")
    raw_path = request_line.split()[1] if len(request_line.split()) >= 2 else "/"
    path = urlparse(raw_path).path
    if path == "/ticks":
        await send_response(writer, {"ticks": FEED.latest, "health": FEED.health()})
        return
    if path == "/candidates":
        await send_response(writer, {"candidates": FEED.candidates, "health": FEED.health(), "sector_heat": FEED.sector_heat})
        return
    if path == "/health":
        await send_response(writer, FEED.health())
        return
    await send_response(writer, {"error": "use /ticks, /candidates or /health"}, status="404 Not Found")


async def send_response(writer: asyncio.StreamWriter, payload: dict, status: str = "200 OK") -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    writer.write(
        f"HTTP/1.1 {status}\r\n".encode("utf-8")
        + b"Content-Type: application/json; charset=utf-8\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
        + body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


def _pct(value: object) -> float:
    if value is None:
        return 0.0
    return float(str(value).strip().replace("%", "") or 0)


def _first_float(row: dict, keys: tuple[str, ...]) -> float:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return float(str(value).strip().replace("%", ""))
        except (TypeError, ValueError):
            continue
    return 0.0


async def main() -> None:
    feed_task = asyncio.create_task(FEED.start())
    server = await asyncio.start_server(handle_client, HOST, PORT)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    print(f"TDX 全市场候选池已启动: http://{HOST}:{PORT}/ticks")
    async with server:
        serve_task = asyncio.create_task(server.serve_forever())
        await stop_event.wait()
        server.close()
        await server.wait_closed()
        serve_task.cancel()
        feed_task.cancel()
        await asyncio.gather(serve_task, feed_task, return_exceptions=True)
    print("TDX 全市场候选池已停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
