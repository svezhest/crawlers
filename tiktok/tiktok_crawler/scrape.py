"""TikTok web scraping without an API key.

Uses the same unauthenticated endpoints the web app calls:
  * profile page -> SSR JSON (`__UNIVERSAL_DATA_FOR_REHYDRATION__`) -> secUid
  * /api/post/item_list/   -> paginated post ids
  * /video/<id> page       -> SSR JSON with desc, stats, tags, image URLs (photo posts)
  * /api/comment/list/ and /api/comment/list/reply/ -> comments + replies

TikTok answers short CAPTCHA pages when it dislikes an IP; those are detected by size
and retried with backoff. A residential/own-network egress (--proxy) helps a lot.
Cookies from the profile page are re-warmed every N requests.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
SSR_RE = re.compile(r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"\s*type="application/json">([^<]+)</script>')
CAPTCHA_THRESHOLD = 10_000
COOKIE_REFRESH_EVERY = 15


class Blocked(Exception):
    pass


class TikTok:
    def __init__(self, username: str, proxy: str | None, delay: float):
        self.user = username.lstrip("@")
        self.profile_url = f"https://www.tiktok.com/@{self.user}"
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Referer": "https://www.tiktok.com/",
                               "Origin": "https://www.tiktok.com"})
        if proxy:
            self.s.proxies = {"http": proxy, "https": proxy}
        self.calls = 0
        self.warm()

    def warm(self) -> None:
        try:
            self.s.get(self.profile_url, timeout=30)
        except requests.RequestException:
            pass

    def _get(self, url: str, **kw) -> requests.Response:
        self.calls += 1
        if self.calls % COOKIE_REFRESH_EVERY == 0:
            self.warm()
        time.sleep(self.delay)
        for attempt in range(3):
            try:
                r = self.s.get(url, timeout=30, **kw)
            except requests.RequestException as e:
                if attempt == 2:
                    raise
                time.sleep(2 * (2 ** attempt))
                continue
            if r.status_code == 200:
                return r
            if r.status_code in (403, 429):
                time.sleep(5 * (2 ** attempt))
                continue
            r.raise_for_status()
        raise Blocked(f"HTTP {r.status_code} on {url}")

    def _ssr(self, url: str) -> dict:
        html = self._get(url).text
        if len(html) < CAPTCHA_THRESHOLD:
            raise Blocked(f"short response ({len(html)} bytes) — CAPTCHA/blocked?")
        m = SSR_RE.search(html)
        if not m:
            raise Blocked("SSR data not found in page")
        return json.loads(m.group(1))

    def discover_posts(self) -> list[str]:
        ssr = self._ssr(self.profile_url)
        sec_uid = (ssr.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
                   .get("userInfo", {}).get("user", {}).get("secUid"))
        if not sec_uid:
            raise Blocked("secUid not found on profile page")
        ids, cursor, more = [], 0, True
        while more:
            data = self._get("https://www.tiktok.com/api/post/item_list/",
                             params={"aid": 1988, "app_language": "en", "app_name": "tiktok_web",
                                     "count": 35, "secUid": sec_uid, "cursor": cursor}).json()
            for it in data.get("itemList") or []:
                if it.get("id"):
                    ids.append(it["id"])
            more = bool(data.get("hasMore"))
            cursor = int(data.get("cursor") or 0)
        print(f"[tiktok] discovered {len(ids)} posts on @{self.user}", file=sys.stderr)
        return ids

    def post_detail(self, post_id: str) -> dict:
        ssr = self._ssr(f"{self.profile_url}/video/{post_id}")
        item = (ssr.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {})
                .get("itemInfo", {}).get("itemStruct"))
        if not item:
            raise Blocked("itemStruct not found")
        stats = item.get("stats") or {}
        images = [((img.get("imageURL") or {}).get("urlList") or [None])[0]
                  for img in ((item.get("imagePost") or {}).get("images") or [])]
        return {"post_id": post_id, "url": f"{self.profile_url}/video/{post_id}",
                "desc": item.get("desc", ""), "create_time": item.get("createTime"),
                "is_photo_post": bool(images),
                "like_count": stats.get("diggCount", 0), "share_count": stats.get("shareCount", 0),
                "collect_count": stats.get("collectCount", 0), "comment_count": stats.get("commentCount", 0),
                "play_count": stats.get("playCount", 0),
                "tags": [c.get("title") for c in item.get("challenges") or [] if c.get("title")],
                "image_urls": [u for u in images if u]}

    @staticmethod
    def _comment(v: dict) -> dict | None:
        cid = v.get("cid")
        if not cid:
            return None
        return {"cid": cid, "user": ((v.get("user") or {}).get("uniqueId")) or "unknown",
                "text": v.get("text", ""), "like_count": v.get("diggCount", 0),
                "reply_count": v.get("replyCommentTotal", 0), "create_time": v.get("createTime"),
                "replies": []}

    def _page(self, url: str, params: dict) -> list[dict]:
        out, cursor, empty = [], 0, 0
        while True:
            data = self._get(url, params={**params, "cursor": cursor}).json()
            arr = data.get("comments") or []
            if not arr:
                empty += 1
                if empty >= 2:
                    break
                continue
            empty = 0
            out.extend(c for c in (self._comment(v) for v in arr) if c)
            if not data.get("hasMore"):
                break
            cursor = int(data.get("cursor") or 0)
        return out

    def comments(self, post_id: str, with_replies: bool) -> list[dict]:
        base = {"aid": 1988, "app_language": "en", "app_name": "tiktok_web", "count": 50, "aweme_id": post_id}
        top = self._page("https://www.tiktok.com/api/comment/list/", base)
        if with_replies:
            for i, c in enumerate(top, 1):
                if c["reply_count"] > 0:
                    try:
                        c["replies"] = self._page("https://www.tiktok.com/api/comment/list/reply/",
                                                  {**base, "comment_id": c["cid"]})
                    except Exception as e:
                        print(f"  replies for {c['cid']}: {type(e).__name__}", file=sys.stderr)
                if i % 50 == 0:
                    print(f"  replies: {i}/{len(top)}", file=sys.stderr)
        return top

    def download_images(self, urls: list[str], dest: Path) -> int:
        dest.mkdir(parents=True, exist_ok=True)
        n = 0
        for i, u in enumerate(urls):
            p = dest / f"{i:02d}.jpg"
            if p.exists():
                n += 1
                continue
            try:
                r = self.s.get(u, timeout=30)
                if r.status_code == 200 and len(r.content) > 1000:
                    p.write_bytes(r.content)
                    n += 1
            except requests.RequestException:
                pass
        return n
