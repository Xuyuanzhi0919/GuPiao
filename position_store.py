from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from user_preferences import normalize_code


class PositionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def payload(self) -> dict[str, list[dict[str, Any]]]:
        return {"positions": sorted(self._data.values(), key=lambda item: item.get("updated_ts", 0), reverse=True)}

    def upsert(self, code: str, name: str = "", sector: str = "", price: str | float = 0, shares: str | int = 0, source: str = "") -> dict[str, list[dict[str, Any]]]:
        normalized = normalize_code(code)
        buy_price = max(0.0, self._float(price))
        share_count = max(0, int(self._float(shares)))
        position_name = name or str(self._data.get(normalized, {}).get("name") or normalized)
        position_sector = sector or str(self._data.get(normalized, {}).get("sector") or "--")
        if not normalized or buy_price <= 0 or share_count <= 0 or _is_etf_position(normalized, position_sector, position_name):
            return self.payload()
        current = self._data.get(normalized, {})
        self._data[normalized] = {
            "code": normalized,
            "name": position_name,
            "sector": position_sector,
            "buy_price": round(buy_price, 3),
            "shares": share_count,
            "source": source or current.get("source") or "manual",
            "updated_ts": time.time(),
        }
        self._save()
        return self.payload()

    def remove(self, code: str) -> dict[str, list[dict[str, Any]]]:
        normalized = normalize_code(code)
        if normalized and normalized in self._data:
            self._data.pop(normalized, None)
            self._save()
        return self.payload()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        rows = raw.get("positions", []) if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            return {}
        data: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = normalize_code(str(row.get("code", "")))
            buy_price = self._float(row.get("buy_price"))
            shares = int(self._float(row.get("shares")))
            name = str(row.get("name") or code)
            sector = str(row.get("sector") or "--")
            if code and buy_price > 0 and shares > 0 and not _is_etf_position(code, sector, name):
                data[code] = {
                    "code": code,
                    "name": name,
                    "sector": sector,
                    "buy_price": round(buy_price, 3),
                    "shares": shares,
                    "source": str(row.get("source") or _default_position_source(code, str(row.get("sector") or ""), str(row.get("name") or ""))),
                    "updated_ts": float(row.get("updated_ts") or 0),
                }
        return data

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.payload(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _float(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


def _default_position_source(code: str, sector: str, name: str) -> str:
    if code.startswith(("000", "001", "002", "003", "600", "601", "603", "605")):
        return "limit-up"
    return "manual"


def _is_etf_position(code: str, sector: str, name: str) -> bool:
    text = f"{sector} {name}".upper()
    return "ETF" in text or code.startswith(("51", "56", "58"))
