"""crawl-reddit — harvest subreddits from the command line.

    crawl-reddit rss Aphantasia LucidDreaming          # no auth, ~25 newest per listing
    crawl-reddit archive Aphantasia --no-comments      # no auth, FULL history via Arctic Shift
    crawl-reddit crawl Aphantasia -q "mind's eye" -q cured   # OAuth (see .env.example)
    crawl-reddit stats
    crawl-reddit youtube-links > yt_ids.txt            # feed to crawl-youtube fetch --file
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .store import Store


def _subs(p: argparse.ArgumentParser) -> None:
    p.add_argument("subreddits", nargs="+", help="subreddit names without r/")


def main() -> None:
    ap = argparse.ArgumentParser(prog="crawl-reddit", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data", help="output directory (default ./data)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    _subs(sub.add_parser("rss", help="anonymous Atom feeds (submissions only)"))

    a_ = sub.add_parser("archive", help="full history via Arctic Shift (no auth)")
    _subs(a_)
    a_.add_argument("--no-comments", action="store_true")
    a_.add_argument("--since", type=int, default=0, help="created_utc cursor to start from")

    c = sub.add_parser("crawl", help="live OAuth crawl: listings + searches + comments")
    _subs(c)
    c.add_argument("-q", "--query", action="append", default=[], help="in-subreddit search (repeatable)")
    c.add_argument("--no-comments", action="store_true")

    sub.add_parser("stats", help="counts in the store")
    sub.add_parser("youtube-links", help="print distinct YouTube ids/urls found in texts")

    a = ap.parse_args()
    store = Store(Path(a.out))
    try:
        if a.cmd == "rss":
            from .rss import run
            run(store, a.subreddits)
        elif a.cmd == "archive":
            from .arctic_shift import run
            run(store, a.subreddits, not a.no_comments, a.since)
        elif a.cmd == "crawl":
            from .crawl import run
            run(store, a.subreddits, a.query, not a.no_comments)
        elif a.cmd == "stats":
            print(store.counts())
        elif a.cmd == "youtube-links":
            rows = store.youtube_links()
            for yid, url in rows:
                print(f"{yid}\t{url}")
            print(f"# {len(rows)} youtube links", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n[reddit] interrupted; progress saved and resumable.", file=sys.stderr)
        sys.exit(130)
    finally:
        store.close()


if __name__ == "__main__":
    main()
