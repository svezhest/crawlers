"""Zero-setup driver: Reddit Atom feeds (~25 newest submissions per listing, no comments)."""
from __future__ import annotations

import html
import re
import sys
import time
import xml.etree.ElementTree as ET

import requests

from . import config
from .store import Document, Store

_NS = {"a": "http://www.w3.org/2005/Atom"}
_TAG_RE = re.compile(r"<[^>]+>")
_LISTINGS = [("new", ""), ("top", "?t=all"), ("hot", "")]


def _fetch(url: str) -> str:
    for attempt in range(config.MAX_RETRIES):
        time.sleep(config.REQUEST_DELAY)
        r = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=45)
        if r.status_code == 200 and "xml" in r.headers.get("content-type", ""):
            return r.text
        wait = config.BACKOFF_BASE * (2 ** attempt)
        print(f"[rss] {r.status_code} on {url}; backoff {wait:.0f}s", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f"rss fetch failed: {url}")


def _doc(entry: ET.Element, sub: str) -> Document:
    def txt(tag: str) -> str:
        el = entry.find(f"a:{tag}", _NS)
        return (el.text or "") if el is not None else ""
    author = entry.find("a:author/a:name", _NS)
    link = entry.find("a:link", _NS)
    return Document(
        fullname=txt("id"), kind="submission", subreddit=sub,
        author=(author.text if author is not None else "").lstrip("/u/"), title=txt("title"),
        text=html.unescape(_TAG_RE.sub("", txt("content"))).strip(),
        permalink=link.get("href") if link is not None else "",
        extra={"updated": txt("updated"), "via": "rss"})


def run(store: Store, subreddits: list[str]) -> None:
    for sub in subreddits:
        print(f"[rss] === r/{sub} ===", file=sys.stderr)
        for listing, qs in _LISTINGS:
            root = ET.fromstring(_fetch(f"https://www.reddit.com/r/{sub}/{listing}/.rss{qs}"))
            entries = root.findall("a:entry", _NS)
            new = sum(store.upsert(_doc(e, sub)) for e in entries)
            print(f"  [{sub}/{listing}] {len(entries)} entries, {new} new", file=sys.stderr)
            store.log(sub, f"rss:{listing}", f"{len(entries)} entries")
    print(f"[rss] done. {store.counts()}", file=sys.stderr)
