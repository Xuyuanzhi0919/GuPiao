from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlparse, urlunparse


@dataclass
class DatabaseStore:
    url: str
    enabled: bool = True
    last_error: str = ""

    @classmethod
    def from_env(cls) -> "DatabaseStore | None":
        raw_url = os.environ.get("DATABASE_URL", "").strip()
        if not raw_url:
            return None
        return cls(normalize_database_url(raw_url))

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "url": mask_database_url(self.url),
            "last_error": self.last_error,
        }

    def save_limit_up_payload(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            psycopg, Json = _load_psycopg()
            self._ensure_database(psycopg)
            with psycopg.connect(self.url, autocommit=True) as connection:
                self._ensure_schema(connection)
                trade_date = _date(payload.get("date"))
                previous_date = _date(payload.get("previous_date"))
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        insert into limit_up_snapshots (trade_date, previous_date, source, summary, payload)
                        values (%s, %s, %s, %s, %s)
                        """,
                        (
                            trade_date,
                            previous_date,
                            str(payload.get("source") or ""),
                            Json(payload.get("summary") or {}),
                            Json(payload),
                        ),
                    )
                    cursor.execute("delete from limit_up_signals where trade_date = %s", (trade_date,))
                    for item in payload.get("signals", []):
                        cursor.execute(
                            """
                            insert into limit_up_signals
                              (trade_date, code, name, sector, action, score, price, payload)
                            values (%s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                trade_date,
                                str(item.get("code") or ""),
                                str(item.get("name") or ""),
                                str(item.get("sector") or ""),
                                str(item.get("action") or ""),
                                _number(item.get("score")),
                                _number(item.get("price")),
                                Json(item),
                            ),
                        )
            self.last_error = ""
        except Exception as error:  # noqa: BLE001 - database is optional, never break trading UI
            self.last_error = f"{error.__class__.__name__}: {error}"

    def save_limit_up_focus(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            psycopg, Json = _load_psycopg()
            self._ensure_database(psycopg)
            with psycopg.connect(self.url, autocommit=True) as connection:
                self._ensure_schema(connection)
                trade_date = _date(payload.get("date"))
                next_date = _date(payload.get("next_date"))
                openclaw = payload.get("openclaw_review") or {}
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        insert into limit_up_focus_reports
                          (trade_date, next_date, source, summary, openclaw_summary, payload)
                        values (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            trade_date,
                            next_date,
                            str(payload.get("source") or ""),
                            Json(payload.get("summary") or {}),
                            Json(openclaw),
                            Json(payload),
                        ),
                    )
                    cursor.execute("delete from limit_up_focus_stocks where trade_date = %s", (trade_date,))
                    for item in payload.get("focus", []):
                        cursor.execute(
                            """
                            insert into limit_up_focus_stocks
                              (trade_date, next_date, code, name, sector, tier, score, payload)
                            values (%s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                trade_date,
                                next_date,
                                str(item.get("code") or ""),
                                str(item.get("name") or ""),
                                str(item.get("sector") or ""),
                                str(item.get("openclaw_tier") or "rule"),
                                _number(item.get("openclaw_score") or item.get("focus_score")),
                                Json(item),
                            ),
                        )
            self.last_error = ""
        except Exception as error:  # noqa: BLE001
            self.last_error = f"{error.__class__.__name__}: {error}"

    def save_next_day_monitor(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            psycopg, Json = _load_psycopg()
            self._ensure_database(psycopg)
            with psycopg.connect(self.url, autocommit=True) as connection:
                self._ensure_schema(connection)
                trade_date = _date(payload.get("date"))
                source_date = _date(payload.get("source_date"))
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        insert into limit_up_next_day_monitors
                          (trade_date, source_date, source, summary, payload)
                        values (%s, %s, %s, %s, %s)
                        """,
                        (
                            trade_date,
                            source_date,
                            str(payload.get("source") or ""),
                            Json(payload.get("summary") or {}),
                            Json(payload),
                        ),
                    )
                    cursor.execute("delete from limit_up_next_day_rows where trade_date = %s and source_date = %s", (trade_date, source_date))
                    for item in payload.get("rows", []):
                        cursor.execute(
                            """
                            insert into limit_up_next_day_rows
                              (trade_date, source_date, code, name, sector, tier, action, score, change_pct, payload)
                            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                trade_date,
                                source_date,
                                str(item.get("code") or ""),
                                str(item.get("name") or ""),
                                str(item.get("sector") or ""),
                                str(item.get("openclaw_tier") or "rule"),
                                str(item.get("action") or ""),
                                _number(item.get("score")),
                                _number(item.get("change_pct")),
                                Json(item),
                            ),
                        )
            self.last_error = ""
        except Exception as error:  # noqa: BLE001
            self.last_error = f"{error.__class__.__name__}: {error}"

    def _ensure_schema(self, connection: Any) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                create table if not exists limit_up_snapshots (
                  id bigserial primary key,
                  trade_date date not null,
                  previous_date date,
                  source text not null default '',
                  summary jsonb not null default '{}'::jsonb,
                  payload jsonb not null,
                  created_at timestamptz not null default now()
                )
                """
            )
            cursor.execute(
                """
                create index if not exists idx_limit_up_snapshots_trade_date
                on limit_up_snapshots (trade_date desc, id desc)
                """
            )
            cursor.execute(
                """
                create table if not exists limit_up_signals (
                  id bigserial primary key,
                  trade_date date not null,
                  code text not null,
                  name text not null default '',
                  sector text not null default '',
                  action text not null default '',
                  score numeric,
                  price numeric,
                  payload jsonb not null,
                  created_at timestamptz not null default now()
                )
                """
            )
            cursor.execute(
                """
                create index if not exists idx_limit_up_signals_trade_date_score
                on limit_up_signals (trade_date desc, score desc)
                """
            )
            cursor.execute(
                """
                create table if not exists limit_up_focus_reports (
                  id bigserial primary key,
                  trade_date date not null,
                  next_date date,
                  source text not null default '',
                  summary jsonb not null default '{}'::jsonb,
                  openclaw_summary jsonb not null default '{}'::jsonb,
                  payload jsonb not null,
                  created_at timestamptz not null default now()
                )
                """
            )
            cursor.execute("create index if not exists idx_limit_up_focus_reports_trade_date on limit_up_focus_reports (trade_date desc, id desc)")
            cursor.execute(
                """
                create table if not exists limit_up_focus_stocks (
                  id bigserial primary key,
                  trade_date date not null,
                  next_date date,
                  code text not null,
                  name text not null default '',
                  sector text not null default '',
                  tier text not null default '',
                  score numeric,
                  payload jsonb not null,
                  created_at timestamptz not null default now()
                )
                """
            )
            cursor.execute("create index if not exists idx_limit_up_focus_stocks_trade_tier on limit_up_focus_stocks (trade_date desc, tier, score desc)")
            cursor.execute(
                """
                create table if not exists limit_up_next_day_monitors (
                  id bigserial primary key,
                  trade_date date not null,
                  source_date date,
                  source text not null default '',
                  summary jsonb not null default '{}'::jsonb,
                  payload jsonb not null,
                  created_at timestamptz not null default now()
                )
                """
            )
            cursor.execute("create index if not exists idx_limit_up_next_day_monitors_trade_date on limit_up_next_day_monitors (trade_date desc, id desc)")
            cursor.execute(
                """
                create table if not exists limit_up_next_day_rows (
                  id bigserial primary key,
                  trade_date date not null,
                  source_date date,
                  code text not null,
                  name text not null default '',
                  sector text not null default '',
                  tier text not null default '',
                  action text not null default '',
                  score numeric,
                  change_pct numeric,
                  payload jsonb not null,
                  created_at timestamptz not null default now()
                )
                """
            )
            cursor.execute("create index if not exists idx_limit_up_next_day_rows_trade_action on limit_up_next_day_rows (trade_date desc, action, score desc)")

    def _ensure_database(self, psycopg: Any) -> None:
        parsed = urlparse(self.url)
        db_name = parsed.path.strip("/") or "gupiao"
        try:
            with psycopg.connect(self.url, connect_timeout=3):
                return
        except Exception as error:
            if "does not exist" not in str(error):
                raise
        admin_url = urlunparse(parsed._replace(path="/postgres"))
        with psycopg.connect(admin_url, autocommit=True, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute("select 1 from pg_database where datname = %s", (db_name,))
                if cursor.fetchone():
                    return
                escaped = db_name.replace('"', '""')
                cursor.execute(f'create database "{escaped}"')


def normalize_database_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    scheme = parsed.scheme.replace("+psycopg", "")
    path = parsed.path if parsed.path and parsed.path != "/" else "/gupiao"
    return urlunparse(parsed._replace(scheme=scheme, path=path))


def mask_database_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    netloc = parsed.netloc
    if "@" in netloc and ":" in netloc.split("@", 1)[0]:
        userinfo, host = netloc.rsplit("@", 1)
        user = userinfo.split(":", 1)[0]
        netloc = f"{user}:***@{host}"
    return urlunparse(parsed._replace(netloc=netloc))


def _load_psycopg() -> tuple[Any, Any]:
    import psycopg
    from psycopg.types.json import Json

    return psycopg, Json


def _date(value: Any) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value))


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
