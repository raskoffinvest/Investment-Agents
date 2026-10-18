"""SQLite storage shared by all agents."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from invagents.timeutil import from_iso, to_iso, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    agent TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY,
    agent TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt TEXT NOT NULL,
    response TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    finish_reason TEXT,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS news_items (
    id TEXT PRIMARY KEY,
    feed TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT NOT NULL,
    description TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    keyword_score INTEGER NOT NULL,
    status TEXT NOT NULL,
    relevance INTEGER,
    title_tr TEXT,
    summary_tr TEXT,
    summary_source TEXT,
    violations TEXT,
    sent_at TEXT
);
CREATE INDEX IF NOT EXISTS news_items_status ON news_items(status);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_cooldowns (
    key TEXT PRIMARY KEY,
    last_fired_at TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # --- runs -------------------------------------------------------------
    def start_run(self, agent: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs(agent, started_at) VALUES (?, ?)", (agent, to_iso(utcnow()))
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, detail: str = "") -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, status=?, detail=? WHERE id=?",
            (to_iso(utcnow()), status, detail, run_id),
        )
        self.conn.commit()

    # --- messages ---------------------------------------------------------
    def log_message(self, agent: str, kind: str, text: str, ok: bool, error: str = "") -> None:
        self.conn.execute(
            "INSERT INTO messages(agent, kind, text, sent_at, ok, error) VALUES (?,?,?,?,?,?)",
            (agent, kind, text, to_iso(utcnow()), int(ok), error),
        )
        self.conn.commit()

    def count_messages_since(
        self, since: datetime, agent: str | None = None, kind: str | None = None
    ) -> int:
        sql = "SELECT COUNT(*) FROM messages WHERE ok=1 AND sent_at >= ?"
        args: list[Any] = [to_iso(since)]
        if agent:
            sql += " AND agent = ?"
            args.append(agent)
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        return int(self.conn.execute(sql, args).fetchone()[0])

    # --- llm --------------------------------------------------------------
    def log_llm_call(self, **row: Any) -> None:
        row.setdefault("created_at", to_iso(utcnow()))
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.conn.execute(f"INSERT INTO llm_calls({cols}) VALUES ({marks})", list(row.values()))
        self.conn.commit()

    def llm_spend_since(self, since: datetime) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE created_at >= ?",
            (to_iso(since),),
        ).fetchone()
        return float(row[0])

    # --- news -------------------------------------------------------------
    def known_news_ids(self, ids: Iterable[str]) -> set[str]:
        ids = list(ids)
        if not ids:
            return set()
        marks = ",".join("?" for _ in ids)
        rows = self.conn.execute(f"SELECT id FROM news_items WHERE id IN ({marks})", ids)
        return {r[0] for r in rows}

    def insert_news(self, item: dict[str, Any]) -> None:
        cols = ", ".join(item)
        marks = ", ".join("?" for _ in item)
        self.conn.execute(
            f"INSERT OR IGNORE INTO news_items({cols}) VALUES ({marks})", list(item.values())
        )
        self.conn.commit()

    def update_news(self, item_id: str, **fields: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE news_items SET {sets} WHERE id=?", [*fields.values(), item_id])
        self.conn.commit()

    def news_by_status(self, status: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM news_items WHERE status=? ORDER BY keyword_score DESC, "
                "published_at DESC",
                (status,),
            )
        )

    # --- snapshots / cooldowns --------------------------------------------
    def save_snapshot(
        self, agent: str, kind: str, source: str, fetched_at: datetime, payload: Any
    ) -> None:
        self.conn.execute(
            "INSERT INTO snapshots(agent, kind, source, fetched_at, payload) VALUES (?,?,?,?,?)",
            (agent, kind, source, to_iso(fetched_at), json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()

    def cooldown_active(self, key: str, hours: float, now: datetime | None = None) -> bool:
        row = self.conn.execute(
            "SELECT last_fired_at FROM alert_cooldowns WHERE key=?", (key,)
        ).fetchone()
        if not row:
            return False
        return (now or utcnow()) - from_iso(row[0]) < timedelta(hours=hours)

    def mark_fired(self, key: str, now: datetime | None = None) -> None:
        self.conn.execute(
            "INSERT INTO alert_cooldowns(key, last_fired_at) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET last_fired_at=excluded.last_fired_at",
            (key, to_iso(now or utcnow())),
        )
        self.conn.commit()
