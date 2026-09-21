"""Browser transport: every request goes through a real Chromium (Playwright).

TikTok fingerprints the TLS stack, so plain `requests` gets the CAPTCHA page even with a
valid cookie jar. Inside the browser everything works, and when TikTok does raise a
CAPTCHA the human at the keyboard solves it in the same window; the scraper just waits.
Profile and video pages are loaded with page.goto; JSON APIs are called with `fetch()`
from inside the page so they carry the page's cookies, headers and fingerprint.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from .scrape import CAPTCHA_THRESHOLD, Blocked



class BrowserTransport:
    def __init__(self, profile_dir: Path, proxy: str | None, delay: float, headless: bool,
                 human_wait: int):
        self.profile_dir = profile_dir
        self.proxy = proxy
        self.delay = delay
        self.headless = headless
        self.human_wait = human_wait
        self._loop = asyncio.new_event_loop()
        self._pw = self._ctx = self._page = None
        self._loop.run_until_complete(self._start())

    async def _start(self) -> None:
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        kw = {"headless": self.headless, "viewport": {"width": 1280, "height": 900}}
        if self.proxy:
            kw["proxy"] = {"server": self.proxy}
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._ctx = await self._pw.chromium.launch_persistent_context(str(self.profile_dir), **kw)
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()

    def close(self) -> None:
        async def _c():
            await self._ctx.close()
            await self._pw.stop()
        self._loop.run_until_complete(_c())

    # ---- helpers ----
    _CAPTCHA_JS = """() => {
      const els = document.querySelectorAll('[id*="captcha" i],[class*="captcha" i],[data-testid*="captcha" i]');
      for (const e of els) { const r = e.getBoundingClientRect(); if (r.width > 50 && r.height > 50) return true; }
      return false; }"""
    _HTML_JS = "() => document.documentElement.outerHTML"

    async def _eval(self, js: str, *args):
        """evaluate() fails while the page is mid-navigation (TikTok redirects a lot); retry."""
        for _ in range(20):
            try:
                return await self._page.evaluate(js, *args)
            except Exception:
                await self._page.wait_for_timeout(500)
        return None

    async def _captcha_visible(self) -> bool:
        return bool(await self._eval(self._CAPTCHA_JS))

    async def _content(self) -> str:
        return (await self._eval(self._HTML_JS)) or ""

    async def _wait_human(self, what: str) -> None:
        """A CAPTCHA modal is on screen: block until it disappears or time runs out."""
        if self.headless:
            raise Blocked(f"{what}: CAPTCHA in headless mode — rerun without --headless and solve it")
        print(f">>> CAPTCHA on {what}. Solve it in the browser window; waiting up to "
              f"{self.human_wait}s...", file=sys.stderr)
        t0 = time.time()
        while time.time() - t0 < self.human_wait:
            await self._page.wait_for_timeout(1500)
            if not await self._captcha_visible():
                print(">>> thanks, continuing.", file=sys.stderr)
                await self._page.wait_for_timeout(1000)
                return
        raise Blocked(f"{what}: CAPTCHA not solved in {self.human_wait}s")

    async def _page_html(self, url: str) -> str:
        await asyncio.sleep(self.delay)
        await self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await self._page.wait_for_timeout(1000)
        if await self._captcha_visible():
            await self._wait_human(url)
        html = await self._content()
        if len(html) < CAPTCHA_THRESHOLD:
            # a bare wall page without the app: give the human a chance, then re-read
            await self._wait_human(url)
            html = await self._content()
        return html

    async def _discover(self, profile_url: str, max_idle_scrolls: int = 4) -> list[dict]:
        """Scroll the profile page and capture the app's own (signed) item_list responses.
        Returns full item structs (desc, stats, imagePost, ...), newest first."""
        items: dict[str, dict] = {}
        state = {"has_more": True}

        async def on_resp(r):
            if "/api/post/item_list/" not in r.url:
                return
            try:
                d = json.loads(await r.text() or "{}")
            except Exception:
                return
            for it in d.get("itemList") or []:
                if it.get("id"):
                    items[it["id"]] = it
            state["has_more"] = bool(d.get("hasMore", True))

        self._page.on("response", on_resp)
        try:
            await self._page_html(profile_url)
            idle = 0
            while state["has_more"] and idle < max_idle_scrolls:
                before = len(items)
                await self._page.mouse.wheel(0, 6000)
                await self._page.wait_for_timeout(2000)
                if await self._captcha_visible():
                    await self._wait_human("profile scroll")
                idle = idle + 1 if len(items) == before else 0
        finally:
            self._page.remove_listener("response", on_resp)
        return list(items.values())

    def discover(self, profile_url: str) -> list[dict]:
        return self._loop.run_until_complete(self._discover(profile_url))

    async def _fetch_json(self, url: str) -> dict:
        await asyncio.sleep(self.delay)
        js = """async (u) => { const r = await fetch(u, {credentials: 'include'});
                              const t = await r.text(); return {status: r.status, text: t}; }"""
        for attempt in range(3):
            res = await self._eval(js, url)
            if res is None:
                await asyncio.sleep(2)
                continue
            if res["status"] == 200 and res["text"].strip():
                try:
                    return json.loads(res["text"])
                except json.JSONDecodeError:
                    pass
            if res["status"] in (403, 429) or not res["text"].strip():
                # TikTok answers APIs with an empty body when the session needs a CAPTCHA.
                # If the modal is not up yet, a reload of the current page brings it.
                if not await self._captcha_visible():
                    await self._page.reload(wait_until="domcontentloaded")
                    await self._page.wait_for_timeout(1500)
                if await self._captcha_visible():
                    await self._wait_human(url)
                continue
            await asyncio.sleep(2 * (2 ** attempt))
        raise Blocked(f"API {url.split('?')[0]} -> HTTP {res['status']}, {len(res['text'])} bytes")

    # ---- sync facade used by TikTok ----
    def page_html(self, url: str) -> str:
        return self._loop.run_until_complete(self._page_html(url))

    def get_json(self, url: str, params: dict) -> dict:
        from urllib.parse import urlencode
        return self._loop.run_until_complete(self._fetch_json(f"{url}?{urlencode(params)}"))

    def get_bytes(self, url: str) -> bytes:
        async def _g():
            r = await self._ctx.request.get(url, timeout=30000)
            return await r.body() if r.status == 200 else b""
        return self._loop.run_until_complete(_g())
