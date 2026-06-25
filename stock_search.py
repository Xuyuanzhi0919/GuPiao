from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from user_preferences import normalize_code

SYMBOLS_PATH = Path(__file__).parent / "data" / "tickdb_cn_symbols.json"
SECTORS_PATH = Path(__file__).parent / "data" / "tdx_stock_sectors.json"


def search_stocks(query: str = "", limit: int = 20) -> dict[str, list[dict[str, Any]]]:
    keyword = str(query or "").strip().lower()
    rows = _load_stocks()
    if keyword:
        rows = [row for row in rows if keyword in f"{row['code']} {row['symbol']} {row['name']} {row.get('sector', '')}".lower()]
    return {"stocks": rows[: max(1, min(limit, 50))]}


def lookup_stocks(codes: list[str] | str = "") -> dict[str, dict[str, Any]]:
    if isinstance(codes, str):
        values = [item.strip() for item in codes.split(",")]
    else:
        values = [str(item).strip() for item in codes]
    wanted = {normalize_code(item.split(".", 1)[0]) for item in values}
    wanted.discard("")
    if not wanted:
        return {"stocks": {}}
    rows = {row["code"]: row for row in _load_stocks()}
    return {"stocks": {code: rows[code] for code in sorted(wanted) if code in rows}}


@lru_cache(maxsize=1)
def _load_stocks() -> list[dict[str, Any]]:
    if not SYMBOLS_PATH.exists():
        return []
    try:
        payload = json.loads(SYMBOLS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    symbols = payload.get("symbols", payload) if isinstance(payload, dict) else payload
    meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
    sectors = _load_sectors()
    rows: list[dict[str, Any]] = []
    for item in symbols if isinstance(symbols, list) else []:
        symbol = str(item.get("symbol") or item.get("code") if isinstance(item, dict) else item)
        code = normalize_code(symbol.split(".", 1)[0])
        if not code:
            continue
        info = meta.get(symbol, {}) if isinstance(meta, dict) else {}
        rows.append(
            {
                "code": code,
                "symbol": symbol,
                "name": str(info.get("name") or code),
                "sector": sectors.get(symbol, "--"),
            }
        )
    return sorted(rows, key=lambda row: (row["code"], row["symbol"]))


def _load_sectors() -> dict[str, str]:
    if not SECTORS_PATH.exists():
        return {}
    try:
        payload = json.loads(SECTORS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    sectors = payload.get("sectors", {}) if isinstance(payload, dict) else {}
    return sectors if isinstance(sectors, dict) else {}
