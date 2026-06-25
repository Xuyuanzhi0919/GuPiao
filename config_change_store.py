from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class ConfigChangeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, before: dict[str, Any], after: dict[str, Any], action: str = "update") -> None:
        changes = {
            key: {"before": before.get(key), "after": after.get(key)}
            for key in sorted(set(before) | set(after))
            if before.get(key) != after.get(key)
        }
        if not changes:
            return
        row = {
            "ts": time.time(),
            "action": action,
            "changes": changes,
            "before": before,
            "after": after,
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")

    def latest(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines[-limit:] if line.strip()]
        return sorted(rows, key=lambda item: item.get("ts", 0), reverse=True)
