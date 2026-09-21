"""SQLite (dedup by Reddit fullname, link registry) + append-only documents.jsonl.

Layout under --out (default ./data):
    crawl.sqlite3       items / links / crawl_log
    documents.jsonl     one normalized Document per line (submissions and comments)
    raw/                verbatim API payloads (only for `crawl`)
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    fullname TEXT PRIMARY KEY, kind TEXT NOT NULL, subreddit TEXT, author TEXT, title TEXT,
    permalink TEXT, created_utc REAL, score INTEGER, link_id TEXT, fetched_utc REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
    url TEXT NOT NULL, from_item TEXT NOT NULL, is_youtube INTEGER NOT NULL DEFAULT 0,
    youtube_id TEXT, PRIMARY KEY (url, from_item)
);
CREATE TABLE IF NOT EXISTS crawl_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, subreddit TEXT, listing TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_sub ON items(subreddit);
CREATE INDEX IF NOT EXISTS idx_links_yt ON links(is_youtube);
"""
_YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/watch\?[^ )\]]*\bv=|youtu\.be/|youtube\.com/(?:shorts|embed|v)/)([A-Za-z0-9_-]{11})")
_URL_RE = re.compile(r"https?://[^\s)\]<>\"']+")


@dataclass
class Document:
    fullname: str
    kind: str                       # 'submission' | 'comment'
    source: str = "reddit"
    subreddit: str = ""
    author: str = ""
    title: str = ""
    text: str = ""
    permalink: str = ""
    created_utc: float = 0.0
    score: int = 0
    link_id: str = ""
    external_urls: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


class Store:
    def __init__(self, out: Path):
        out.mkdir(parents=True, exist_ok=True)
        self.out = out
        self.raw_dir = out / "raw"
        self.jsonl = out / "documents.jsonl"
        self.conn = sqlite3.connect(out / "crawl.sqlite3", timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def has(self, fullname: str) -> bool:
        return self.conn.execute("SELECT 1 FROM items WHERE fullname=?", (fullname,)).fetchone() is not None

    def log(self, subreddit: str, listing: str, note: str) -> None:
        self.conn.execute("INSERT INTO crawl_log (ts, subreddit, listing, note) VALUES (?,?,?,?)",
                          (time.time(), subreddit, listing, note))
        self.conn.commit()

    def dump_raw(self, name: str, payload) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        (self.raw_dir / f"{name}-{int(time.time())}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _register_links(self, doc: Document) -> None:
        hay = f"{doc.text}\n{doc.extra.get('url', '')}"
        for url in set(_URL_RE.findall(hay)) | set(doc.external_urls):
            m = _YOUTUBE_RE.search(url)
            self.conn.execute(
                "INSERT OR IGNORE INTO links (url, from_item, is_youtube, youtube_id) VALUES (?,?,?,?)",
                (url, doc.fullname, 1 if m else 0, m.group(1) if m else None))

    def upsert(self, doc: Document) -> bool:
        """Insert or refresh; append to JSONL only when new. Returns True if new."""
        new = not self.has(doc.fullname)
        self.conn.execute(
            "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?,?,?,?,?)",
            (doc.fullname, doc.kind, doc.subreddit, doc.author, doc.title, doc.permalink,
             doc.created_utc, doc.score, doc.link_id, time.time()))
        self._register_links(doc)
        if new:
            with self.jsonl.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(doc), ensure_ascii=False) + "\n")
        self.conn.commit()
        return new

    def counts(self) -> dict:
        by_kind = dict(self.conn.execute("SELECT kind, COUNT(*) FROM items GROUP BY kind").fetchall())
        by_sub = dict(self.conn.execute("SELECT subreddit, COUNT(*) FROM items GROUP BY subreddit").fetchall())
        yt = self.conn.execute("SELECT COUNT(DISTINCT youtube_id) FROM links WHERE is_youtube=1").fetchone()[0]
        return {"by_kind": by_kind, "by_subreddit": by_sub, "youtube_links": yt}

    def youtube_links(self) -> list[tuple[str, str]]:
        return self.conn.execute(
            "SELECT DISTINCT youtube_id, url FROM links WHERE is_youtube=1 ORDER BY youtube_id").fetchall()
