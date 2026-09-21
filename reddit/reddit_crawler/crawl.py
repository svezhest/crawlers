"""OAuth driver: listings (new/top-all/hot) + in-subreddit searches + comment trees."""
from __future__ import annotations

import sys

from . import config
from .client import RedditClient
from .docs import comment_doc, submission_doc
from .store import Store


def _walk_comments(children: list, store: Store) -> int:
    new, stack = 0, list(children)
    while stack:
        node = stack.pop()
        if node.get("kind") != "t1":
            continue  # "more" stubs are skipped to stay light
        data = node.get("data", {})
        new += store.upsert(comment_doc(data, "api"))
        replies = data.get("replies")
        if isinstance(replies, dict):
            stack.extend(replies.get("data", {}).get("children", []))
    return new


def crawl_listing(c: RedditClient, store: Store, sub: str, listing: str, params: dict) -> list[str]:
    seen, after = [], None
    for page in range(config.MAX_PAGES_PER_LISTING):
        p = dict(params, limit=config.LISTING_PAGE_SIZE)
        if after:
            p["after"] = after
        payload = c.get(f"r/{sub}/{listing}", p)
        store.dump_raw(f"{sub}-{listing}-p{page}", payload)
        children = payload.get("data", {}).get("children", [])
        if not children:
            break
        new = 0
        for ch in children:
            if ch.get("kind") == "t3":
                doc = submission_doc(ch["data"], "api")
                new += store.upsert(doc)
                seen.append(doc.fullname)
        after = payload.get("data", {}).get("after")
        print(f"  [{sub}/{listing}] page {page}: {len(children)} items, {new} new", file=sys.stderr)
        if not after:
            break
    store.log(sub, listing, f"{len(seen)} submissions")
    return seen


def crawl_search(c: RedditClient, store: Store, sub: str, query: str) -> list[str]:
    payload = c.get(f"r/{sub}/search", {"q": query, "restrict_sr": 1, "sort": "relevance",
                                        "t": "all", "limit": config.LISTING_PAGE_SIZE})
    store.dump_raw(f"{sub}-search-{query.replace(' ', '_')}", payload)
    seen = []
    for ch in payload.get("data", {}).get("children", []):
        if ch.get("kind") == "t3":
            doc = submission_doc(ch["data"], "api")
            store.upsert(doc)
            seen.append(doc.fullname)
    store.log(sub, f"search:{query}", f"{len(seen)} hits")
    return seen


def fetch_comments(c: RedditClient, store: Store, sub: str, fullname: str) -> int:
    sid = fullname.split("_", 1)[-1]
    payload = c.get(f"r/{sub}/comments/{sid}", {"limit": 500, "depth": 10, "sort": "top"})
    store.dump_raw(f"{sub}-comments-{sid}", payload)
    if not isinstance(payload, list) or len(payload) < 2:
        return 0
    return _walk_comments(payload[1].get("data", {}).get("children", []), store)


def run(store: Store, subreddits: list[str], queries: list[str], with_comments: bool) -> None:
    c = RedditClient()
    for sub in subreddits:
        print(f"[crawl] === r/{sub} ===", file=sys.stderr)
        seen: set[str] = set()
        for listing, params in config.LISTINGS:
            seen.update(crawl_listing(c, store, sub, listing, params))
        for q in queries:
            seen.update(crawl_search(c, store, sub, q))
        if with_comments:
            print(f"[crawl] comments for {len(seen)} submissions", file=sys.stderr)
            for i, fn in enumerate(sorted(seen), 1):
                fetch_comments(c, store, sub, fn)
                if i % 25 == 0:
                    print(f"  ...{i}/{len(seen)}", file=sys.stderr)
    print(f"[crawl] done. {store.counts()}", file=sys.stderr)
