"""OAuth client for oauth.reddit.com (client_credentials, read-only public data).

Reddit no longer serves its JSON API anonymously, so this driver needs a free "script"
app: REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET in the environment or ./.env.
"""
from __future__ import annotations

import os
import random
import sys
import time

import requests

from . import config


class RedditClient:
    def __init__(self) -> None:
        self.client_id = os.environ.get("REDDIT_CLIENT_ID")
        self.client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
        if not (self.client_id and self.client_secret):
            raise SystemExit(
                "`crawl` needs Reddit OAuth: set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET "
                "(free 'script' app at https://www.reddit.com/prefs/apps), or use `rss` / `archive`.")
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT
        self._token_expiry = 0.0
        self._last = 0.0

    def _ensure_token(self) -> None:
        if time.time() < self._token_expiry - 60:
            return
        r = requests.post("https://www.reddit.com/api/v1/access_token",
                          auth=(self.client_id, self.client_secret),
                          data={"grant_type": "client_credentials"},
                          headers={"User-Agent": config.USER_AGENT}, timeout=30)
        r.raise_for_status()
        tok = r.json()
        self._token_expiry = time.time() + tok.get("expires_in", 3600)
        self.session.headers["Authorization"] = f"bearer {tok['access_token']}"

    def _throttle(self) -> None:
        wait = config.REQUEST_DELAY + random.uniform(0, config.JITTER) - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def get(self, path: str, params: dict | None = None):
        self._ensure_token()
        params = dict(params or {})
        params.setdefault("raw_json", 1)
        url = f"https://oauth.reddit.com/{path.lstrip('/')}"
        for attempt in range(config.MAX_RETRIES):
            self._throttle()
            try:
                r = self.session.get(url, params=params, timeout=45)
            except requests.RequestException as exc:
                wait = config.BACKOFF_BASE * (2 ** attempt)
                print(f"[client] {exc!r}; retry in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401:
                self._token_expiry = 0
                self._ensure_token()
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                ra = r.headers.get("Retry-After")
                wait = float(ra) if ra else config.BACKOFF_BASE * (2 ** attempt)
                print(f"[client] {r.status_code} on {path}; backoff {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
        raise RuntimeError(f"giving up on {path} after {config.MAX_RETRIES} retries")
