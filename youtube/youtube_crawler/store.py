"""SQLite state + on-disk outputs. Resumable: every id gets a terminal or retryable status.

Layout under --out (default ./data):
    transcripts/<video_id>.txt    plain text, one cue per line
    transcripts.jsonl             one normalized document per transcript
    state.sqlite3                 per-id status so nothing is fetched twice
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

TERMINAL = {"ok", "disabled", "none", "unavailable", "invalid"}
RETRYABLE = {"blocked", "error"}


class Store:
    def __init__(self, out: Path):
        self.out = out
        self.txt_dir = out / "transcripts"
        self.jsonl = out / "transcripts.jsonl"
        self.txt_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(out / "state.sqlite3", timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS transcripts (
                video_id TEXT PRIMARY KEY,
                status   TEXT NOT NULL,
                lang     TEXT,
                chars    INTEGER,
                error    TEXT,
                fetched_utc INTEGER
            )""")
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def status(self, vid: str) -> str | None:
        row = self.conn.execute("SELECT status FROM transcripts WHERE video_id=?", (vid,)).fetchone()
        return row[0] if row else None

    def pending(self, ids: list[str], retry: bool) -> list[str]:
        """Ids still worth fetching. Fresh ids first, previously blocked/errored last."""
        fresh, again = [], []
        for vid in ids:
            st = self.status(vid)
            if st in TERMINAL:
                continue
            (again if st in RETRYABLE else fresh).append(vid)
        return fresh + (again if retry else [])

    def record(self, vid: str, status: str, *, lang: str | None = None,
               chars: int = 0, error: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO transcripts VALUES (?,?,?,?,?,?)",
            (vid, status, lang, chars, (error or "")[:300], int(time.time())))
        self.conn.commit()

    def write_ok(self, vid: str, text: str, lang: str) -> int:
        (self.txt_dir / f"{vid}.txt").write_text(text, encoding="utf-8")
        doc = {"id": f"yt_{vid}", "source": "youtube", "video_id": vid, "lang": lang,
               "url": f"https://youtu.be/{vid}", "text": text,
               "fetched_utc": int(time.time())}
        with self.jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
        self.record(vid, "ok", lang=lang, chars=len(text))
        return len(text)

    def counts(self) -> dict:
        return dict(self.conn.execute(
            "SELECT status, COUNT(*) FROM transcripts GROUP BY status").fetchall())
