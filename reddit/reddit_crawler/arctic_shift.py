"""Full-history driver via the Arctic Shift archive (https://arctic-shift.photon-reddit.com).

No auth, no 1000-item cap: posts AND comments of a whole subreddit, paginated by
created_utc (sort=asc, limit=100, cursor advanced with a 1 s overlap; the Store dedups).
This is a third-party mirror of the public Reddit dumps, so be reasonable with pacing.
"""
from __future__ import annotations

import sys
import time

import requests

from . import config
from .docs import comment_doc, submission_doc
from .store import Store

BASE = "https://arctic-shift.photon-reddit.com"
PAGE = 100


def _get(kind: str, subreddit: str, after: int) -> list[dict]:
    params = {"subreddit": subreddit, "limit": PAGE, "sort": "asc"}
    if after > 0:
        params["after"] = after
    for attempt in range(config.MAX_RETRIES):
        time.sleep(config.ARCHIVE_DELAY)
        try:
            r = requests.get(f"{BASE}/api/{kind}/search", params=params,
                             headers={"User-Agent": config.USER_AGENT}, timeout=90)
        except requests.RequestException as exc:
            wait = config.BACKOFF_BASE * (2 ** attempt)
            print(f"[archive] {exc!r}; retry {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            continue
        if r.status_code == 200:
            return r.json().get("data") or []
        wait = config.BACKOFF_BASE * (2 ** attempt)
        print(f"[archive] {r.status_code} {r.text[:120]}; backoff {wait:.0f}s", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f"archive fetch failed: {kind}/{subreddit}@{after}")


def harvest(kind: str, subreddit: str, store: Store, since: int = 0) -> int:
    mk = submission_doc if kind == "posts" else comment_doc
    after, total, page = since, 0, 0
    while True:
        batch = _get(kind, subreddit, after)
        if not batch:
            break
        new, max_utc = 0, after
        for item in batch:
            doc = mk(item, "arctic_shift")
            new += store.upsert(doc)
            max_utc = max(max_utc, int(doc.created_utc))
        total += new
        page += 1
        print(f"  [{subreddit}/{kind}] page {page}: +{new} (total {total}), cursor {max_utc}",
              file=sys.stderr)
        if len(batch) < PAGE:
            break
        after = max(max_utc - 1, after + 1)
    store.log(subreddit, f"archive:{kind}", f"{total} new")
    return total


def run(store: Store, subreddits: list[str], with_comments: bool, since: int = 0) -> None:
    for sub in subreddits:
        print(f"[archive] === r/{sub}: posts ===", file=sys.stderr)
        harvest("posts", sub, store, since)
        if with_comments:
            print(f"[archive] === r/{sub}: comments ===", file=sys.stderr)
            harvest("comments", sub, store, since)
    print(f"[archive] done. {store.counts()}", file=sys.stderr)
