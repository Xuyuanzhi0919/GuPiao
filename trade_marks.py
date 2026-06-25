from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from user_preferences import normalize_code

ALLOWED_MARKS = {"bought", "wait_pullback", "passed"}


class TradeMarkStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def payload(self) -> dict[str, dict[str, Any]]:
        return {"marks": dict(sorted(self._data.items()))}

    def set(self, code: str, mark: str) -> dict[str, dict[str, Any]]:
        normalized = normalize_code(code)
        if not normalized:
            return self.payload()
        if mark not in ALLOWED_MARKS:
            return self.remove(normalized)
        self._data[normalized] = {"mark": mark, "updated_ts": time.time()}
        self._save()
        return self.payload()

    def remove(self, code: str) -> dict[str, dict[str, Any]]:
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
        marks = raw.get("marks") if isinstance(raw, dict) else raw
        if not isinstance(marks, dict):
            return {}
        data: dict[str, dict[str, Any]] = {}
        for code, value in marks.items():
            normalized = normalize_code(str(code))
            if not normalized:
                continue
            if isinstance(value, str):
                mark = value
                updated_ts = 0
            elif isinstance(value, dict):
                mark = str(value.get("mark", ""))
                updated_ts = float(value.get("updated_ts") or 0)
            else:
                continue
            if mark in ALLOWED_MARKS:
                data[normalized] = {"mark": mark, "updated_ts": updated_ts}
        return data

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.payload(), ensure_ascii=False, indent=2), encoding="utf-8")
