from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class UserPreferenceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def payload(self) -> dict[str, list[str]]:
        return {
            "watchlist": sorted(self._data["watchlist"]),
            "blocklist": sorted(self._data["blocklist"]),
        }

    def watchlist(self) -> set[str]:
        return set(self._data["watchlist"])

    def add(self, list_name: str, code: str) -> dict[str, list[str]]:
        target = self._target(list_name)
        normalized = normalize_code(code)
        if not normalized:
            return self.payload()
        other = "blocklist" if target == "watchlist" else "watchlist"
        self._data[target].add(normalized)
        self._data[other].discard(normalized)
        self._save()
        return self.payload()

    def remove(self, list_name: str, code: str) -> dict[str, list[str]]:
        target = self._target(list_name)
        normalized = normalize_code(code)
        if normalized:
            self._data[target].discard(normalized)
            self._save()
        return self.payload()

    def replace(self, watchlist: list[str] | None = None, blocklist: list[str] | None = None) -> dict[str, list[str]]:
        next_watch = {normalize_code(code) for code in (watchlist or [])}
        next_block = {normalize_code(code) for code in (blocklist or [])}
        next_watch.discard("")
        next_block.discard("")
        overlap = next_watch & next_block
        next_block -= overlap
        self._data = {"watchlist": next_watch, "blocklist": next_block}
        self._save()
        return self.payload()

    def _target(self, list_name: str) -> str:
        return "blocklist" if list_name == "blocklist" else "watchlist"

    def _load(self) -> dict[str, set[str]]:
        if not self.path.exists():
            return {"watchlist": set(), "blocklist": set()}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"watchlist": set(), "blocklist": set()}
        return {
            "watchlist": self._read_set(raw, "watchlist"),
            "blocklist": self._read_set(raw, "blocklist"),
        }

    def _read_set(self, raw: dict[str, Any], key: str) -> set[str]:
        values = raw.get(key, [])
        if not isinstance(values, list):
            return set()
        return {code for code in (normalize_code(str(item)) for item in values) if code}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.payload(), ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_code(code: str) -> str:
    return "".join(ch for ch in str(code).strip() if ch.isdigit())[:6]
