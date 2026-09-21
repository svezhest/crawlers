"""crawl-youtube — download YouTube transcripts from the command line.

    crawl-youtube fetch https://youtu.be/dQw4w9WgXcQ VIDEO_ID ...
    crawl-youtube fetch --file ids.txt --max 40 --langs ru,en
    crawl-youtube fetch --file ids.txt --proxy-ports 20001,20002,20003
    crawl-youtube login                      # optional: sign in once for the fast path
    crawl-youtube panel --file ids.txt --workers 20
    crawl-youtube stats
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .ids import collect_ids
from .store import Store


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def _ids_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("items", nargs="*", help="YouTube URLs or 11-char video ids")
    p.add_argument("--file", "-f", action="append", default=[],
                   help="file with one URL/id per line (repeatable)")
    p.add_argument("--retry", action="store_true",
                   help="also retry ids previously marked blocked/error")
    p.add_argument("--max", type=int, help="stop after this many NEW transcripts")


def main() -> None:
    _load_dotenv(Path.cwd() / ".env")
    ap = argparse.ArgumentParser(prog="crawl-youtube", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data", help="output directory (default ./data)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="anonymous API driver (slow, polite, no login)")
    _ids_args(f)
    f.add_argument("--langs", default="ru,en", help="preferred languages, comma-separated")
    f.add_argument("--delay", type=float, default=float(os.environ.get("YT_DELAY", "20")))
    f.add_argument("--jitter", type=float, default=float(os.environ.get("YT_JITTER", "10")))
    f.add_argument("--timeout", type=float, default=60.0)
    f.add_argument("--proxy-ports", default=os.environ.get("YT_PROXY_PORTS", ""),
                   help="local SOCKS5 ports to rotate across, comma-separated")

    lg = sub.add_parser("login", help="open Chromium to sign into YouTube once (panel driver)")
    lg.add_argument("--wait", type=int, default=120, help="seconds the window stays open")

    pn = sub.add_parser("panel", help="logged-in get_panel driver (fast; needs `login`)")
    _ids_args(pn)
    pn.add_argument("--workers", type=int, default=20)

    sub.add_parser("stats", help="print per-status counts")

    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if a.cmd == "login":
        from . import panel
        panel.login(out, a.wait)
        return

    store = Store(out)
    try:
        if a.cmd == "stats":
            print(store.counts())
            return
        ids = store.pending(collect_ids(a.items, a.file), retry=a.retry)
        if not ids:
            print("[yt] nothing pending.", file=sys.stderr)
            return
        if a.max is not None and a.cmd == "panel":
            ids = ids[: a.max]
        print(f"[yt] {len(ids)} id(s) pending", file=sys.stderr)
        if a.cmd == "fetch":
            from . import api
            ports = [int(p) for p in a.proxy_ports.split(",") if p.strip()]
            api.run(store, ids, langs=[s for s in a.langs.split(",") if s], delay=a.delay,
                    jitter=a.jitter, max_new=a.max, ports=ports, timeout=a.timeout)
        elif a.cmd == "panel":
            from . import panel
            panel.run(store, ids, workers=a.workers, out=out)
    except KeyboardInterrupt:
        print("\n[yt] interrupted; progress saved.", file=sys.stderr)
        sys.exit(130)
    finally:
        store.close()


if __name__ == "__main__":
    main()
