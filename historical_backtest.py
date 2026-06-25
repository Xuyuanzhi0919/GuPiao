from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from market_clock import next_trading_date


SYMBOLS_CACHE = Path("data/tickdb_cn_symbols.json")
HISTORY_CACHE_DIR = Path("data/history_cache")
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_TRENDS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
EASTMONEY_ZT_POOL_URL = "https://push2ex.eastmoney.com/getTopicZTPool"


@dataclass
class MinuteBar:
    ts: str
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float
    prev_close: float = 0.0


def rapid_rise_history_backtest(params: dict, progress: callable | None = None) -> dict:
    date = str(params.get("date") or "").strip()
    if not date:
        raise ValueError("date is required, format YYYY-MM-DD")
    codes = _select_codes(params)
    rise_1m = float(params.get("rise_1m", 0.7) or 0.7)
    rise_3m = float(params.get("rise_3m", 1.2) or 1.2)
    min_amount_2m = float(params.get("min_amount_2m", 5_000_000) or 0)
    max_day_change = float(params.get("max_day_change", 7.5) or 7.5)
    cooldown_min = int(params.get("cooldown_min", 10) or 10)
    max_signals = int(params.get("max_signals", 300) or 300)
    require_limit_up = bool(params.get("require_limit_up", False))
    limit_up_pool = {item["symbol"]: item for item in fetch_eastmoney_limit_up_pool(date)} if require_limit_up else {}

    signals = []
    errors = []
    scanned = 0
    next_day = next_trading_date(date)
    for symbol in codes:
        try:
            bars = fetch_eastmoney_minute_bars(symbol, date)
            limit_info = limit_up_pool.get(symbol)
            if require_limit_up and not limit_info:
                continue
            scanned += 1
            symbol_signals = detect_rapid_rise(
                symbol=symbol,
                bars=bars,
                rise_1m=rise_1m,
                rise_3m=rise_3m,
                min_amount_2m=min_amount_2m,
                max_day_change=max_day_change,
                cooldown_min=cooldown_min,
                require_limit_up=require_limit_up,
                limit_info=limit_info,
            )
            if symbol_signals:
                try:
                    next_bars = fetch_eastmoney_minute_bars(symbol, next_day)
                except Exception:
                    next_bars = []
                for signal in symbol_signals:
                    signal.update(_next_day_returns(signal["entry_price"], next_day, next_bars))
            signals.extend(symbol_signals)
        except Exception as error:
            if len(errors) < 12:
                errors.append({"symbol": symbol, "error": f"{error.__class__.__name__}: {error}"})
        if len(signals) >= max_signals:
            break
        if progress:
            progress(scanned=scanned, total=len(codes), message=f"{date} {symbol}")

    signals = sorted(signals, key=lambda item: (item["score"], item["amount_2m"]), reverse=True)[:max_signals]
    return {
        "params": {
            "date": date,
            "code_count": len(codes),
            "rise_1m": rise_1m,
            "rise_3m": rise_3m,
            "min_amount_2m": min_amount_2m,
            "max_day_change": max_day_change,
            "cooldown_min": cooldown_min,
            "next_day": next_day,
            "require_limit_up": require_limit_up,
            "limit_pool_count": len(limit_up_pool),
        },
        "summary": _summary(signals),
        "by_symbol": _by_symbol(signals),
        "signals": signals,
        "scanned_count": scanned,
        "error_count": len(errors),
        "errors": errors,
    }


def rapid_rise_multi_date_backtest(params: dict, progress: callable | None = None) -> dict:
    dates = _parse_dates(params)
    if not dates:
        return rapid_rise_history_backtest(params)

    all_signals = []
    by_date = []
    all_errors = []
    scanned_count = 0
    limit = int(params.get("max_signals", 300) or 300)
    per_date_limit = max(1, min(limit, int(params.get("per_date_signals", "80") or 80)))

    for date in dates:
        day_params = dict(params)
        day_params["date"] = date
        day_params["max_signals"] = per_date_limit
        payload = rapid_rise_history_backtest(
            day_params,
            progress=(lambda **state: progress(date=date, **state)) if progress else None,
        )
        day_summary = dict(payload["summary"])
        day_summary["date"] = date
        day_summary["next_day"] = payload.get("params", {}).get("next_day", "")
        day_summary["scanned_count"] = payload.get("scanned_count", 0)
        day_summary["error_count"] = payload.get("error_count", 0)
        by_date.append(day_summary)
        scanned_count += payload.get("scanned_count", 0)
        all_errors.extend(payload.get("errors", []))
        for signal in payload.get("signals", []):
            signal["source_date"] = date
            all_signals.append(signal)

    all_signals = sorted(all_signals, key=lambda item: (item.get("source_date", ""), item.get("score", 0)), reverse=True)[:limit]
    return {
        "params": {
            "dates": dates,
            "date_count": len(dates),
            "code_count": int(params.get("max_symbols", 50) or 50),
            "rise_1m": float(params.get("rise_1m", 0.7) or 0.7),
            "rise_3m": float(params.get("rise_3m", 1.2) or 1.2),
            "min_amount_2m": float(params.get("min_amount_2m", 5_000_000) or 0),
            "require_limit_up": bool(params.get("require_limit_up", False)),
        },
        "summary": _summary(all_signals),
        "by_date": by_date,
        "by_symbol": _by_symbol(all_signals),
        "signals": all_signals,
        "scanned_count": scanned_count,
        "error_count": len(all_errors),
        "errors": all_errors[:20],
    }


def fetch_eastmoney_minute_bars(symbol: str, date: str) -> list[MinuteBar]:
    cached = _read_bars_cache(symbol, date)
    if cached is not None:
        return cached
    code = symbol.split(".", 1)[0]
    secid = f"{_market_id(symbol)}.{code}"
    begin = date.replace("-", "") + "093000"
    end = date.replace("-", "") + "150000"
    query = urlencode(
        {
            "secid": secid,
            "klt": "1",
            "fqt": "0",
            "beg": begin,
            "end": end,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }
    )
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://quote.eastmoney.com/",
    }
    payload = None
    last_error: Exception | None = None
    url = f"{EASTMONEY_KLINE_URL}?{query}"
    for attempt in range(3):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except Exception as error:
            last_error = error
            time.sleep(0.4 * (attempt + 1))
    if payload is None:
        curl_cmd = [
            "/usr/bin/curl",
            "-L",
            "--compressed",
            "--retry",
            "2",
            "--retry-delay",
            "1",
            "-A",
            headers["User-Agent"],
            "-e",
            headers["Referer"],
            "-m",
            "12",
            "-s",
            url,
        ]
        for attempt in range(3):
            try:
                completed = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=15)
                raw = completed.stdout.strip()
                if completed.returncode == 0 and raw:
                    payload = json.loads(raw)
                    break
                last_error = RuntimeError(f"curl exit {completed.returncode}: {completed.stderr.strip() or 'empty response'}")
            except Exception as error:
                last_error = error
            time.sleep(0.5 * (attempt + 1))
    if payload is None:
        bars = fetch_eastmoney_trend_bars(symbol, date, use_cache=False)
        _write_bars_cache(symbol, date, bars)
        return bars
    klines = (payload.get("data") or {}).get("klines") or []
    prev_close = float((payload.get("data") or {}).get("preKPrice") or 0)
    bars = []
    for row in klines:
        parts = str(row).split(",")
        if len(parts) < 7:
            continue
        bars.append(
            MinuteBar(
                ts=parts[0],
                open=float(parts[1]),
                close=float(parts[2]),
                high=float(parts[3]),
                low=float(parts[4]),
                volume=float(parts[5]),
                amount=float(parts[6]),
                prev_close=prev_close,
            )
        )
    _write_bars_cache(symbol, date, bars)
    return bars


def fetch_eastmoney_trend_bars(symbol: str, date: str, use_cache: bool = True) -> list[MinuteBar]:
    if use_cache:
        cached = _read_bars_cache(symbol, date)
        if cached is not None:
            return cached
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
    raw = subprocess.check_output(
        [
            "/usr/bin/curl",
            "-L",
            "--compressed",
            "--retry",
            "2",
            "--retry-delay",
            "1",
            "-A",
            "Mozilla/5.0",
            "-e",
            "https://quote.eastmoney.com/",
            "-m",
            "12",
            "-s",
            url,
        ],
        text=True,
    )
    payload = json.loads(raw)
    data = payload.get("data") or {}
    prev_close = float(data.get("preClose") or 0)
    rows = data.get("trends") or []
    bars = []
    prefix = date.replace("-", "")
    for row in rows:
        parts = str(row).split(",")
        if len(parts) < 7:
            continue
        ts = parts[0]
        if ts[:10].replace("-", "") != prefix:
            continue
        open_price = float(parts[1] or 0)
        close = float(parts[2] or 0)
        high = float(parts[3] or close)
        low = float(parts[4] or close)
        if open_price <= 0:
            open_price = close
        bars.append(
            MinuteBar(
                ts=ts,
                open=open_price,
                close=close,
                high=high,
                low=low,
                volume=float(parts[5] or 0),
                amount=float(parts[6] or 0),
                prev_close=prev_close,
            )
        )
    _write_bars_cache(symbol, date, bars)
    return bars


def detect_rapid_rise(
    symbol: str,
    bars: list[MinuteBar],
    rise_1m: float,
    rise_3m: float,
    min_amount_2m: float,
    max_day_change: float,
    cooldown_min: int,
    require_limit_up: bool,
    limit_info: dict | None = None,
) -> list[dict]:
    if len(bars) < 16:
        return []
    signals = []
    last_index = -10_000
    prev_close = bars[0].prev_close or bars[0].open
    limit_threshold = _limit_threshold(symbol)
    limit_index = _first_limit_up_index(bars, prev_close, limit_threshold)
    limit_time = str((limit_info or {}).get("first_limit_time") or "")
    if require_limit_up and not limit_info:
        return []
    for index in range(3, len(bars) - 10):
        current = bars[index]
        base_1m = bars[index - 1].close
        base_3m = bars[index - 3].close
        change_1m = _pct(current.close, base_1m)
        change_3m = _pct(current.close, base_3m)
        day_change = _pct(current.close, prev_close)
        amount_2m = bars[index].amount + bars[index - 1].amount
        if index - last_index < cooldown_min:
            continue
        if day_change > max_day_change:
            continue
        if amount_2m < min_amount_2m:
            continue
        if change_1m < rise_1m and change_3m < rise_3m:
            continue
        if require_limit_up and limit_time and current.ts[-5:] > limit_time[-5:]:
            continue
        signals.append(
            _signal(
                symbol,
                bars,
                index,
                change_1m,
                change_3m,
                day_change,
                amount_2m,
                limit_index,
                limit_threshold,
                prev_close,
                limit_info,
            )
        )
        last_index = index
    return signals


def _signal(
    symbol: str,
    bars: list[MinuteBar],
    index: int,
    change_1m: float,
    change_3m: float,
    day_change: float,
    amount_2m: float,
    limit_index: int | None,
    limit_threshold: float,
    prev_close: float,
    limit_info: dict | None = None,
) -> dict:
    entry = bars[index].close
    future = bars[index + 1 :]
    returns = {
        "ret_1m_pct": _future_close_return(future, entry, 1),
        "ret_3m_pct": _future_close_return(future, entry, 3),
        "ret_5m_pct": _future_close_return(future, entry, 5),
        "ret_10m_pct": _future_close_return(future, entry, 10),
        "high_10m_pct": _future_high_return(future, entry, 10),
        "low_10m_pct": _future_low_return(future, entry, 10),
        "next_day_date": "",
        "next_open_return_pct": "",
        "next_high_return_pct": "",
        "next_low_return_pct": "",
        "next_close_return_pct": "",
        "next_open_to_high_pct": "",
        "next_open_to_close_pct": "",
    }
    score = round(change_1m * 35 + change_3m * 20 + min(amount_2m / 1_000_000, 30) - max(day_change - 5, 0) * 8, 2)
    return {
        "symbol": symbol,
        "code": symbol.split(".", 1)[0],
        "time": bars[index].ts,
        "entry_price": round(entry, 3),
        "rise_1m_pct": change_1m,
        "rise_3m_pct": change_3m,
        "day_change_pct": day_change,
        "amount_2m": round(amount_2m, 2),
        "score": score,
        "limit_up": bool(limit_info) or limit_index is not None,
        "limit_up_time": (limit_info or {}).get("first_limit_time") or (bars[limit_index].ts if limit_index is not None else ""),
        "limit_up_threshold_pct": limit_threshold,
        "limit_up_high_pct": _pct(max(bar.high for bar in bars), prev_close),
        **returns,
    }


def _limit_threshold(symbol: str) -> float:
    code = symbol.split(".", 1)[0]
    if code.startswith(("30", "68")):
        return 19.7
    if code.startswith(("4", "8", "9")) and symbol.endswith(".BJ"):
        return 29.5
    return 9.75


def _first_limit_up_index(bars: list[MinuteBar], prev_close: float, threshold: float) -> int | None:
    if prev_close <= 0:
        return None
    for index, bar in enumerate(bars):
        if _pct(bar.high, prev_close) >= threshold:
            return index
    return None


def _select_codes(params: dict) -> list[str]:
    raw_codes = str(params.get("codes") or "").strip()
    max_symbols = int(params.get("max_symbols", 50) or 50)
    date = str(params.get("date") or "").strip()
    require_limit_up = bool(params.get("require_limit_up", False))
    include_bj = bool(params.get("include_bj", False))
    include_gem = bool(params.get("include_gem", False))
    include_star = bool(params.get("include_star", False))
    if raw_codes:
        return [_normalize_symbol(code) for code in raw_codes.replace("\n", ",").split(",") if code.strip()][:max_symbols]

    if require_limit_up and date:
        return [
            symbol
            for symbol in fetch_eastmoney_limit_up_symbols(date)
            if _board_allowed(symbol, include_bj=include_bj, include_gem=include_gem, include_star=include_star)
        ][:max_symbols]

    rows = _load_symbol_cache()
    selected = []
    for symbol in rows:
        normalized = _normalize_symbol(symbol)
        if not _board_allowed(normalized, include_bj=include_bj, include_gem=include_gem, include_star=include_star):
            continue
        selected.append(normalized)
        if len(selected) >= max_symbols:
            break
    return selected


def _parse_dates(params: dict) -> list[str]:
    raw = str(params.get("dates") or "").strip()
    if not raw:
        return []
    dates = []
    for item in raw.replace("\n", ",").replace("，", ",").split(","):
        value = item.strip()
        if not value:
            continue
        if len(value) == 8 and value.isdigit():
            value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
        dates.append(value)
    return list(dict.fromkeys(dates))[:20]


def fetch_eastmoney_limit_up_symbols(date: str) -> list[str]:
    return [item["symbol"] for item in fetch_eastmoney_limit_up_pool(date)]


def fetch_eastmoney_limit_up_pool(date: str) -> list[dict]:
    cached = _read_json_cache("limit_pool", date)
    if isinstance(cached, list) and all(isinstance(item, dict) and "consecutive_limit_count" in item and "sector" in item for item in cached):
        return cached
    trade_date = date.replace("-", "")
    query = urlencode(
        {
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "dpt": "wz.ztzt",
            "Pageindex": "0",
            "pagesize": "10000",
            "sort": "fbt:asc",
            "date": trade_date,
            "_": str(int(time.time() * 1000)),
        }
    )
    url = f"{EASTMONEY_ZT_POOL_URL}?{query}"
    raw = subprocess.check_output(
        [
            "/usr/bin/curl",
            "-L",
            "--compressed",
            "--retry",
            "2",
            "--retry-delay",
            "1",
            "-A",
            "Mozilla/5.0",
            "-e",
            "https://quote.eastmoney.com/",
            "-m",
            "12",
            "-s",
            url,
        ],
        text=True,
    )
    payload = json.loads(raw)
    pool = (payload.get("data") or {}).get("pool") or []
    rows = []
    for item in pool:
        if not isinstance(item, dict):
            continue
        code = str(item.get("c") or "").strip()
        market = int(item.get("m") or 0)
        if not code:
            continue
        suffix = "SH" if market == 1 or code.startswith("6") else "BJ" if code.startswith(("4", "8", "9")) else "SZ"
        rows.append(
            {
                "symbol": f"{code}.{suffix}",
                "code": code,
                "name": item.get("n", code),
                "sector": item.get("hybk") or item.get("bk") or "未分组",
                "first_limit_time": _format_limit_time(item.get("fbt")),
                "last_limit_time": _format_limit_time(item.get("lbt")),
                "limit_count": int(item.get("lbc") or 0),
                "consecutive_limit_count": int(item.get("lbc") or 0),
                "stat_days": ((item.get("zttj") or {}).get("days") if isinstance(item.get("zttj"), dict) else 0) or 0,
                "stat_limit_count": ((item.get("zttj") or {}).get("ct") if isinstance(item.get("zttj"), dict) else 0) or 0,
                "open_board_count": int(item.get("zbc") or 0),
                "seal_amount": float(item.get("fund") or 0),
                "amount": float(item.get("amount") or 0),
            }
        )
    _write_json_cache("limit_pool", date, rows)
    return rows


def _format_limit_time(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.zfill(6)
    return f"{text[:2]}:{text[2:4]}"


def _bars_cache_path(symbol: str, date: str) -> Path:
    safe = symbol.replace(".", "_")
    return HISTORY_CACHE_DIR / "bars" / f"{date}_{safe}.json"


def _json_cache_path(kind: str, date: str) -> Path:
    return HISTORY_CACHE_DIR / kind / f"{date}.json"


def _read_bars_cache(symbol: str, date: str) -> list[MinuteBar] | None:
    path = _bars_cache_path(symbol, date)
    if not path.exists():
        return None
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        return [MinuteBar(**row) for row in rows]
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_bars_cache(symbol: str, date: str, bars: list[MinuteBar]) -> None:
    if not bars:
        return
    path = _bars_cache_path(symbol, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [bar.__dict__ for bar in bars]
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def _read_json_cache(kind: str, date: str) -> object | None:
    path = _json_cache_path(kind, date)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json_cache(kind: str, date: str, payload: object) -> None:
    path = _json_cache_path(kind, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _board_allowed(symbol: str, include_bj: bool, include_gem: bool, include_star: bool) -> bool:
    code = symbol.split(".", 1)[0]
    if not include_bj and symbol.endswith(".BJ"):
        return False
    if not include_gem and code.startswith("30"):
        return False
    if not include_star and code.startswith("68"):
        return False
    return True


def _load_symbol_cache() -> list[str]:
    if not SYMBOLS_CACHE.exists():
        return ["600000.SH", "000001.SZ", "600030.SH", "600519.SH", "000858.SZ"]
    payload = json.loads(SYMBOLS_CACHE.read_text(encoding="utf-8"))
    rows = payload.get("symbols", payload) if isinstance(payload, dict) else payload
    return [str(item.get("symbol") or item.get("code") if isinstance(item, dict) else item) for item in rows]


def _normalize_symbol(value: str) -> str:
    text = value.strip().upper()
    if "." in text:
        return text
    if text.startswith(("6", "9")):
        return f"{text}.SH"
    if text.startswith(("4", "8")):
        return f"{text}.BJ"
    return f"{text}.SZ"


def _market_id(symbol: str) -> str:
    if symbol.endswith(".SH"):
        return "1"
    return "0"


def _future_close_return(future: list[MinuteBar], entry: float, minutes: int) -> float:
    if len(future) < minutes:
        return 0.0
    return _pct(future[minutes - 1].close, entry)


def _future_high_return(future: list[MinuteBar], entry: float, minutes: int) -> float:
    if not future:
        return 0.0
    high = max(bar.high for bar in future[:minutes])
    return _pct(high, entry)


def _future_low_return(future: list[MinuteBar], entry: float, minutes: int) -> float:
    if not future:
        return 0.0
    low = min(bar.low for bar in future[:minutes])
    return _pct(low, entry)


def _next_day_returns(entry: float, next_day: str, bars: list[MinuteBar]) -> dict:
    if not bars:
        return {
            "next_day_date": next_day,
            "next_open_return_pct": "",
            "next_high_return_pct": "",
            "next_low_return_pct": "",
            "next_close_return_pct": "",
            "next_open_to_high_pct": "",
            "next_open_to_close_pct": "",
        }
    open_price = bars[0].open
    high = max(bar.high for bar in bars)
    low = min(bar.low for bar in bars)
    close = bars[-1].close
    return {
        "next_day_date": next_day,
        "next_open_return_pct": _pct(open_price, entry),
        "next_high_return_pct": _pct(high, entry),
        "next_low_return_pct": _pct(low, entry),
        "next_close_return_pct": _pct(close, entry),
        "next_open_to_high_pct": _pct(high, open_price),
        "next_open_to_close_pct": _pct(close, open_price),
    }


def _summary(signals: list[dict]) -> dict:
    return {
        "sample_count": len(signals),
        "win_1m": _win_rate(signals, "ret_1m_pct"),
        "win_3m": _win_rate(signals, "ret_3m_pct"),
        "win_5m": _win_rate(signals, "ret_5m_pct"),
        "win_10m": _win_rate(signals, "ret_10m_pct"),
        "avg_1m_pct": _avg(signals, "ret_1m_pct"),
        "avg_3m_pct": _avg(signals, "ret_3m_pct"),
        "avg_5m_pct": _avg(signals, "ret_5m_pct"),
        "avg_10m_pct": _avg(signals, "ret_10m_pct"),
        "avg_high_10m_pct": _avg(signals, "high_10m_pct"),
        "avg_low_10m_pct": _avg(signals, "low_10m_pct"),
        "next_day_count": sum(1 for row in signals if row.get("next_close_return_pct") not in ("", None)),
        "next_open_win": _win_rate(signals, "next_open_return_pct"),
        "next_close_win": _win_rate(signals, "next_close_return_pct"),
        "avg_next_open_pct": _avg(signals, "next_open_return_pct"),
        "avg_next_high_pct": _avg(signals, "next_high_return_pct"),
        "avg_next_low_pct": _avg(signals, "next_low_return_pct"),
        "avg_next_close_pct": _avg(signals, "next_close_return_pct"),
    }


def _by_symbol(signals: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for item in signals:
        grouped.setdefault(item["symbol"], []).append(item)
    rows = []
    for symbol, items in grouped.items():
        summary = _summary(items)
        summary["symbol"] = symbol
        summary["code"] = symbol.split(".", 1)[0]
        rows.append(summary)
    return sorted(rows, key=lambda item: (item["sample_count"], item["avg_5m_pct"]), reverse=True)[:50]


def _win_rate(rows: list[dict], key: str) -> float:
    valid = [row for row in rows if row.get(key) not in ("", None)]
    if not valid:
        return 0.0
    return round(sum(1 for row in valid if float(row.get(key) or 0) > 0) / len(valid) * 100, 1)


def _avg(rows: list[dict], key: str) -> float:
    valid = [row for row in rows if row.get(key) not in ("", None)]
    if not valid:
        return 0.0
    return round(sum(float(row.get(key) or 0) for row in valid) / len(valid), 2)


def _pct(price: float, base: float) -> float:
    return round((price / max(base, 0.01) - 1) * 100, 2)
