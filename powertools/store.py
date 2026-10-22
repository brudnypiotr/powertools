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
CREATE TABLE IF NOT EXISTS cursors (
  kind TEXT NOT NULL,
  key TEXT NOT NULL,
  newest TEXT NOT NULL,
  exported_at REAL NOT NULL,
  PRIMARY KEY (kind, key)
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

    # ------------------------------------------------------------------ export cursors ("only new since last time")
    def cursor(self, kind: str, key: str) -> str:
        with self._lock:
            row = self._db.execute("SELECT newest FROM cursors WHERE kind = ? AND key = ?", (kind, key)).fetchone()
            return row["newest"] if row else ""

    def cursors(self, kind: str) -> dict[str, dict]:
        with self._lock:
            return {r["key"]: {"newest": r["newest"], "exported_at": r["exported_at"]}
                    for r in self._db.execute("SELECT * FROM cursors WHERE kind = ?", (kind,))}

    def set_cursor(self, kind: str, key: str, newest: str) -> None:
        with self._lock:
            old = self._db.execute("SELECT newest FROM cursors WHERE kind = ? AND key = ?", (kind, key)).fetchone()
            if old and old["newest"] >= newest:
                newest = old["newest"]
            self._db.execute("INSERT OR REPLACE INTO cursors (kind, key, newest, exported_at) VALUES (?, ?, ?, ?)",
                             (kind, key, newest, time.time()))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
