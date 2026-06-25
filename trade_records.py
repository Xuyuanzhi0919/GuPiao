from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from user_preferences import normalize_code


class TradeRecordStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records = self._load()

    def payload(self, limit: int = 100) -> dict[str, list[dict[str, Any]]]:
        rows = sorted(self._records, key=lambda item: item.get("ts", 0), reverse=True)
        return {"records": rows[: max(1, min(limit, 500))]}

    def add(
        self,
        code: str,
        name: str = "",
        sector: str = "",
        side: str = "",
        price: str | float = 0,
        shares: str | int = 0,
        reason: str = "",
        source: str = "",
    ) -> dict[str, list[dict[str, Any]]]:
        normalized = normalize_code(code)
        trade_price = self._float(price)
        share_count = max(0, int(self._float(shares)))
        if not normalized or trade_price <= 0 or share_count <= 0:
            return self.payload()
        record = {
            "id": f"{int(time.time() * 1000)}-{normalized}-{len(self._records)}",
            "ts": time.time(),
            "code": normalized,
            "name": name or normalized,
            "sector": sector or "--",
            "side": side or "trade",
            "price": round(trade_price, 3),
            "shares": share_count,
            "amount": round(trade_price * share_count, 2),
            "reason": reason,
            "source": source,
        }
        self._records.insert(0, record)
        self._records = self._records[:1000]
        self._save()
        return self.payload()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        rows = raw.get("records", []) if isinstance(raw, dict) else raw
        return [row for row in rows if isinstance(row, dict)]

    def _save(self) -> None:
        self.path.write_text(json.dumps({"records": self._records}, ensure_ascii=False, indent=2), encoding="utf-8")

    def _float(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
