"""crawl-tiktok — download a TikTok profile: posts, comments (+replies), photo-post images.

A real Chromium window opens (Playwright). When TikTok shows a CAPTCHA, solve it there;
the crawler waits and then continues.

    crawl-tiktok profile @someuser
    crawl-tiktok profile someuser --photos-only --with-images --proxy http://127.0.0.1:20171
    crawl-tiktok profile someuser --no-comments --max 50
    crawl-tiktok stats someuser

Output: <out>/tiktok/<user>/posts.jsonl (one post per line incl. comments), images under
<out>/tiktok/<user>/<post_id>/images/, and progress.json for resumable runs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .scrape import Blocked, TikTok


def _load_dotenv(path: Path) -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def main() -> None:
    _load_dotenv(Path.cwd() / ".env")
    ap = argparse.ArgumentParser(prog="crawl-tiktok", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("profile", help="scrape all posts of one profile")
    p.add_argument("username")
    p.add_argument("--out", default="data", help="output directory (default ./data)")
    p.add_argument("--proxy", default=os.environ.get("TIKTOK_PROXY") or None)
    p.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    p.add_argument("--max", type=int, help="stop after N newly processed posts")
    p.add_argument("--no-comments", action="store_true")
    p.add_argument("--no-replies", action="store_true")
    p.add_argument("--with-images", action="store_true", help="download photo-post images")
    p.add_argument("--photos-only", action="store_true", help="skip video-only posts")
    p.add_argument("--retry-errors", action="store_true", help="retry posts that errored before")
    p.add_argument("--driver", choices=["requests", "browser"], default="browser",
                   help="browser (default): real Chromium, you solve CAPTCHAs by hand; "
                        "requests: plain HTTP, only works from IPs TikTok trusts")
    p.add_argument("--headless", action="store_true", help="browser driver without a window (no CAPTCHA solving)")
    p.add_argument("--human-wait", type=int, default=300, help="seconds to wait for a CAPTCHA to be solved")
    s = sub.add_parser("stats", help="progress summary for a profile")
    s.add_argument("username")
    s.add_argument("--out", default="data")
    a = ap.parse_args()

    user = a.username.lstrip("@")
    root = Path(a.out) / "tiktok" / user
    root.mkdir(parents=True, exist_ok=True)
    prog_path, posts_path = root / "progress.json", root / "posts.jsonl"
    progress: dict[str, str] = json.loads(prog_path.read_text()) if prog_path.exists() else {}

    if a.cmd == "stats":
        by = {}
        for v in progress.values():
            by[v.split(":")[0]] = by.get(v.split(":")[0], 0) + 1
        print({"posts_tracked": len(progress), "by_status": by, "posts_file": str(posts_path)})
        return

    def save() -> None:
        tmp = prog_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(progress, indent=1))
        tmp.replace(prog_path)

    browser = None
    if a.driver == "browser":
        from .browser import BrowserTransport
        browser = BrowserTransport(Path(a.out) / "tiktok" / "browser_profile", a.proxy, a.delay,
                                   headless=a.headless, human_wait=a.human_wait)
        tt = TikTok(user, a.proxy, a.delay, browser=browser)
    else:
        tt = TikTok(user, a.proxy, a.delay)
    details: dict[str, dict] = {}   # browser driver: post records already known from the feed
    try:
        if browser is not None:
            for it in tt.discover_items():
                details[it["id"]] = TikTok.item_to_post(it, tt.profile_url)
            ids = list(details)
        else:
            ids = tt.discover_posts()
    except Blocked as e:
        print(f"[tiktok] discovery failed ({e}); falling back to known ids", file=sys.stderr)
        ids = list(progress)
    if not ids:
        if browser is not None:
            browser.close()
        raise SystemExit("[tiktok] no posts found")

    done = 0
    try:
        for i, pid in enumerate(ids, 1):
            st = progress.get(pid, "")
            if st == "ok" or st.startswith("skip") or (st.startswith("err") and not a.retry_errors):
                continue
            if a.max is not None and done >= a.max:
                break
            print(f"[{i}/{len(ids)}] {pid}", file=sys.stderr)
            try:
                post = details.get(pid) or tt.post_detail(pid)
                if a.photos_only and not post["is_photo_post"]:
                    progress[pid] = "skip:video-only"
                    continue
                if a.with_images and post["image_urls"]:
                    post["images_downloaded"] = tt.download_images(post["image_urls"], root / pid / "images")
                post["comments"] = [] if a.no_comments else tt.comments(pid, not a.no_replies)
                post["source"], post["username"], post["fetched_utc"] = "tiktok", user, int(time.time())
                with posts_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(post, ensure_ascii=False) + "\n")
                progress[pid] = "ok"
                done += 1
                print(f"  ok: {len(post['comments'])} comments, {len(post['image_urls'])} images", file=sys.stderr)
            except Blocked as e:
                progress[pid] = f"err:{e}"
                print(f"  blocked: {e} — pausing 30s", file=sys.stderr)
                time.sleep(30)
            except Exception as e:
                progress[pid] = f"err:{type(e).__name__}: {e}"[:200]
                print(f"  error: {type(e).__name__}: {e}", file=sys.stderr)
            finally:
                save()
    except KeyboardInterrupt:
        save()
        print("\n[tiktok] interrupted; progress saved.", file=sys.stderr)
        sys.exit(130)
    finally:
        if browser is not None:
            browser.close()
    save()
    by = {}
    for v in progress.values():
        by[v.split(":")[0]] = by.get(v.split(":")[0], 0) + 1
    print(f"[tiktok] done: {done} new this run. {by}", file=sys.stderr)


if __name__ == "__main__":
    main()
