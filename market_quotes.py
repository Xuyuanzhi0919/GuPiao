from __future__ import annotations

import json
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any

from user_preferences import normalize_code

EASTMONEY_QUOTES_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
SINA_QUOTES_URL = "https://hq.sinajs.cn/list="


def fetch_market_quotes(codes: list[str] | str) -> dict[str, Any]:
    normalized = _normalize_codes(codes)
    if not normalized:
        return {"quotes": {}, "source": "eastmoney", "ts": time.time()}
    secids = ",".join(_secid(code) for code in normalized)
    query = urlencode(
        {
            "secids": secids,
            "fields": "f12,f14,f2,f3,f4,f5,f6,f13,f15,f16,f17,f18,f20,f21",
        }
    )
    request = Request(
        f"{EASTMONEY_QUOTES_URL}?{query}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://quote.eastmoney.com/",
        },
    )
    try:
        payload = _fetch_json(request, f"{EASTMONEY_QUOTES_URL}?{query}")
    except Exception:
        return _fetch_sina_quotes(normalized)
    rows = ((payload.get("data") or {}).get("diff") or []) if isinstance(payload, dict) else []
    quotes = {}
    for row in rows:
        code = normalize_code(str(row.get("f12") or ""))
        price = _scaled_price(code, row.get("f2"))
        prev_close = _scaled_price(code, row.get("f18"))
        if not code or price <= 0:
            continue
        quotes[code] = {
            "code": code,
            "name": str(row.get("f14") or code),
            "price": price,
            "change_pct": _scaled_pct(row.get("f3")),
            "change": _scaled_price(code, row.get("f4")),
            "open": _scaled_price(code, row.get("f17")),
            "high": _scaled_price(code, row.get("f15")),
            "low": _scaled_price(code, row.get("f16")),
            "prev_close": prev_close,
            "volume": _number(row.get("f5")),
            "amount": _number(row.get("f6")),
            "market_value": _number(row.get("f20")),
            "float_market_value": _number(row.get("f21")),
            "source": "eastmoney",
            "ts": time.time(),
        }
    if len(quotes) < len(normalized):
        fallback = _fetch_sina_quotes([code for code in normalized if code not in quotes])
        quotes.update(fallback.get("quotes") or {})
    return {"quotes": quotes, "source": "eastmoney", "ts": time.time()}


def _normalize_codes(codes: list[str] | str) -> list[str]:
    values = codes.split(",") if isinstance(codes, str) else codes
    output = []
    for item in values:
        code = normalize_code(str(item).split(".", 1)[0])
        if code and code not in output:
            output.append(code)
    return output[:300]


def _secid(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return f"1.{code}"
    return f"0.{code}"


def _fetch_sina_quotes(codes: list[str]) -> dict[str, Any]:
    symbols = ",".join(_sina_symbol(code) for code in codes)
    if not symbols:
        return {"quotes": {}, "source": "sina", "ts": time.time()}
    request = Request(
        f"{SINA_QUOTES_URL}{symbols}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/",
        },
    )
    raw = ""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=5) as response:
                raw = response.read().decode("gbk", errors="ignore")
            break
        except Exception as error:
            last_error = error
            time.sleep(0.25 * (attempt + 1))
    if not raw and last_error:
        raise last_error
    quotes = {}
    now = time.time()
    for line in raw.splitlines():
        if '="' not in line:
            continue
        symbol = line.split("=", 1)[0].rsplit("_", 1)[-1]
        code = normalize_code(symbol[-6:])
        parts = line.split('"', 2)[1].split(",")
        if len(parts) < 32 or not code:
            continue
        price = _number(parts[3])
        prev_close = _number(parts[2])
        if price <= 0:
            continue
        change = price - prev_close if prev_close else 0.0
        quotes[code] = {
            "code": code,
            "name": parts[0] or code,
            "price": round(price, 3),
            "change_pct": round((change / prev_close) * 100, 3) if prev_close else 0.0,
            "change": round(change, 3),
            "open": round(_number(parts[1]), 3),
            "high": round(_number(parts[4]), 3),
            "low": round(_number(parts[5]), 3),
            "prev_close": round(prev_close, 3),
            "volume": _number(parts[8]),
            "amount": _number(parts[9]),
            "source": "sina",
            "ts": now,
        }
    return {"quotes": quotes, "source": "sina", "ts": now}


def _sina_symbol(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def _scaled_pct(value: Any) -> float:
    number = _number(value)
    if number <= -100000:
        return 0.0
    return round(number / 100, 3)


def _scaled_price(code: str, value: Any) -> float:
    number = _number(value)
    if number <= -100000:
        return 0.0
    divisor = 1000 if _is_exchange_traded_fund(code) else 100
    return round(number / divisor, 3)


def _is_exchange_traded_fund(code: str) -> bool:
    return code.startswith(("15", "16", "51", "56", "58"))


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fetch_json(request: Request, url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:
            last_error = error
            time.sleep(0.25 * (attempt + 1))
    curl_cmd = [
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
        "8",
        "-s",
        url,
    ]
    completed = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=10)
    raw = completed.stdout.strip()
    if completed.returncode == 0 and raw:
        return json.loads(raw)
    if last_error:
        raise last_error
    raise RuntimeError("quote request failed")
