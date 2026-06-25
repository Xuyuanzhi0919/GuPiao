from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from monitor import Signal


class SignalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[tuple[str, int, str]] = set()

    def append(self, signals: Iterable[Signal]) -> None:
        rows = []
        for signal in signals:
            key = (signal.code, int(signal.ts), signal.grade)
            if key in self._seen:
                continue
            self._seen.add(key)
            rows.append(signal.to_dict())

        if not rows:
            return

        with self.path.open("a", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")

    def latest(self, limit: int = 200) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines[-limit:] if line.strip()]
        return sorted(rows, key=lambda item: item["ts"], reverse=True)

    def export_csv(self, target: Path) -> Path:
        rows = self.latest(limit=10000)
        target.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "ts",
            "grade",
            "score",
            "code",
            "name",
            "sector",
            "price",
            "change_pct",
            "rise_1m_pct",
            "rise_3m_pct",
            "rise_5m_pct",
            "turnover_1m",
            "turnover_today",
            "volume_spike",
            "active_buy_ratio",
            "order_book_bias",
            "sector_heat",
            "distance_to_limit_pct",
            "quality_tags",
            "reasons",
        ]
        with target.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                row = {field: row.get(field, "") for field in fields}
                row["quality_tags"] = (
                    " / ".join(row["quality_tags"]) if isinstance(row["quality_tags"], list) else row["quality_tags"]
                )
                row["reasons"] = " / ".join(row["reasons"]) if isinstance(row["reasons"], list) else row["reasons"]
                writer.writerow(row)
        return target


class TrackStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last_written: dict[tuple[str, float], tuple[int, float]] = {}

    def append(self, tracks: Iterable[dict]) -> None:
        rows = []
        for track in tracks:
            key = (track["code"], float(track["trigger_ts"]))
            marker = (int(track.get("age_sec", 0)), float(track.get("current_return_pct", 0)))
            if self._last_written.get(key) == marker:
                continue
            self._last_written[key] = marker
            rows.append(dict(track))

        if not rows:
            return

        with self.path.open("a", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")

    def latest(self, limit: int = 1000) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines[-limit:] if line.strip()]
        latest_by_track: dict[tuple[str, float], dict] = {}
        for row in rows:
            latest_by_track[(row["code"], float(row["trigger_ts"]))] = row
        return sorted(latest_by_track.values(), key=lambda item: item["trigger_ts"], reverse=True)
