"""Reddit JSON (API or Arctic Shift, same field names) -> Document."""
from __future__ import annotations

from .store import Document


def submission_doc(d: dict, via: str) -> Document:
    url = d.get("url", "") or ""
    return Document(
        fullname=d.get("name") or f"t3_{d.get('id')}", kind="submission",
        subreddit=d.get("subreddit", "") or "", author=d.get("author", "") or "",
        title=d.get("title", "") or "", text=d.get("selftext", "") or "",
        permalink="https://www.reddit.com" + (d.get("permalink", "") or ""),
        created_utc=float(d.get("created_utc", 0) or 0), score=int(d.get("score", 0) or 0),
        external_urls=[url] if url.startswith("http") and not d.get("is_self") else [],
        extra={"num_comments": d.get("num_comments", 0), "url": url,
               "link_flair": d.get("link_flair_text"), "via": via})


def comment_doc(d: dict, via: str) -> Document:
    return Document(
        fullname=d.get("name") or f"t1_{d.get('id')}", kind="comment",
        subreddit=d.get("subreddit", "") or "", author=d.get("author", "") or "",
        text=d.get("body", "") or "",
        permalink="https://www.reddit.com" + (d.get("permalink", "") or ""),
        created_utc=float(d.get("created_utc", 0) or 0), score=int(d.get("score", 0) or 0),
        link_id=d.get("link_id", "") or "", extra={"parent_id": d.get("parent_id"), "via": via})
