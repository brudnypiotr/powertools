"""Dictation history in SQLite (text only, no audio)."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  raw TEXT NOT NULL,
  text TEXT NOT NULL,
  language TEXT,
  audio_s REAL,
  took_s REAL
);
"""


class Store:
    def __init__(self, path: Path):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def add(self, raw: str, text: str, language: str = "", audio_s: float = 0, took_s: float = 0) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO dictations (ts, raw, text, language, audio_s, took_s) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), raw, text, language, round(audio_s, 2), round(took_s, 2)))
            self._db.commit()
            return int(cur.lastrowid)

    def recent(self, limit: int = 50, query: str = "") -> list[dict]:
        sql, args = "SELECT * FROM dictations", []
        if query:
            sql += " WHERE text LIKE ?"
            args.append(f"%{query}%")
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args)]

    def delete(self, item_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM dictations WHERE id = ?", (int(item_id),))
            self._db.commit()
            return cur.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._db.close()
