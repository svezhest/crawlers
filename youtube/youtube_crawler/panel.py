"""Driver 2 (optional, needs `playwright`): logged-in `youtubei/v1/get_panel` requests.

The anonymous timedtext endpoint is IP-throttled; the transcript *panel* the watch page
uses is account-bound instead. Sign in once (`crawl-youtube login`, persistent Chromium
profile under --out/browser_profile), then this driver POSTs the panel request directly:
no page load, ~0.4 s/video, parallel. It stops instantly on 401/403/429 to protect the
account. Requires an account you own and are willing to use for this.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from .store import Store

ORIGIN = "https://www.youtube.com"
CLIENT_VER = os.environ.get("YT_CLIENT_VER", "2.20260901.00.00")
PANEL_ID = "PAmodern_transcript_view"
ENDPOINT = f"{ORIGIN}/youtubei/v1/get_panel?prettyPrint=false"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


def _varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _field(fnum: int, wire: int, payload: bytes) -> bytes:
    return _varint((fnum << 3) | wire) + payload


def _params(vid: str) -> str:
    inner = _field(1, 2, _varint(len(vid)) + vid.encode()) + _field(3, 0, _varint(2))
    return base64.b64encode(_field(149, 2, _varint(len(inner)) + inner)).decode()


def _auth(cookies: dict) -> str:
    ts = int(time.time())
    parts = []
    for name, label in (("SAPISID", "SAPISIDHASH"), ("__Secure-1PAPISID", "SAPISID1PHASH"),
                        ("__Secure-3PAPISID", "SAPISID3PHASH")):
        sid = cookies.get(name) or cookies.get("SAPISID")
        if sid:
            h = hashlib.sha1(f"{ts} {sid} {ORIGIN}".encode()).hexdigest()
            parts.append(f"{label} {ts}_{h}_u")
    return " ".join(parts)


def _cues(data) -> list[str]:
    out: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            sv = o.get("transcriptSegmentViewModel")
            if isinstance(sv, dict) and sv.get("simpleText"):
                out.append(sv["simpleText"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return out


def _guess_lang(text: str) -> str:
    head = text[:2000]
    cyr = sum(1 for c in head if "\u0400" <= c <= "\u04ff")
    return "ru" if cyr > len(head) * 0.2 else "en"


def _profile(out: Path) -> Path:
    return out / "browser_profile"


async def _login_async(out: Path, wait_s: int) -> None:
    from playwright.async_api import async_playwright
    prof = _profile(out)
    prof.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(str(prof), headless=False, user_agent=UA)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(ORIGIN, wait_until="domcontentloaded")
        print(f">>> Sign into YouTube in the window. Closing in {wait_s}s.", file=sys.stderr)
        await page.wait_for_timeout(wait_s * 1000)
        await ctx.close()
    print(f"[yt-panel] session saved in {prof}", file=sys.stderr)


def login(out: Path, wait_s: int = 120) -> None:
    asyncio.run(_login_async(out, wait_s))


async def _run_async(store: Store, ids: list[str], workers: int, out: Path) -> None:
    from playwright.async_api import async_playwright
    prof = _profile(out)
    if not prof.exists():
        raise SystemExit("[yt-panel] no browser profile — run `crawl-youtube login` first.")
    q: asyncio.Queue = asyncio.Queue()
    for v in ids:
        q.put_nowait(v)
    stop = asyncio.Event()
    got = {"ok": 0}
    t0 = time.monotonic()

    async with async_playwright() as p:
        pctx = await p.chromium.launch_persistent_context(str(prof), headless=True, user_agent=UA)
        storage = await pctx.storage_state()
        await pctx.close()
        cookies = {c["name"]: c["value"] for c in storage.get("cookies", [])}
        if "SAPISID" not in cookies:
            raise SystemExit("[yt-panel] profile is not signed in — run `crawl-youtube login`.")
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(storage_state=storage, user_agent=UA)

        async def one(vid: str) -> tuple[str, object]:
            headers = {"authorization": _auth(cookies), "x-goog-authuser": "0", "x-origin": ORIGIN,
                       "content-type": "application/json", "x-youtube-client-name": "1",
                       "x-youtube-client-version": CLIENT_VER, "origin": ORIGIN,
                       "referer": f"{ORIGIN}/watch?v={vid}"}
            body = {"context": {"client": {"clientName": "WEB", "clientVersion": CLIENT_VER}},
                    "panelId": PANEL_ID, "params": _params(vid)}
            try:
                r = await ctx.request.post(ENDPOINT, headers=headers, data=json.dumps(body), timeout=30000)
            except Exception as e:
                return "error", f"post: {type(e).__name__}"
            if r.status in (401, 403, 429):
                return "throttle", r.status
            if r.status != 200:
                return "error", f"http{r.status}"
            try:
                cues = _cues(json.loads(await r.text()))
            except Exception as e:
                return "error", f"parse: {type(e).__name__}"
            text = "\n".join(s for s in (c.strip() for c in cues) if s)
            if not text:
                return "none", "no transcript"
            return "ok", (text, _guess_lang(text))

        async def worker() -> None:
            while not stop.is_set():
                try:
                    vid = q.get_nowait()
                except asyncio.QueueEmpty:
                    return
                status, detail = await one(vid)
                if status == "ok":
                    text, lang = detail
                    store.write_ok(vid, text, lang)
                    got["ok"] += 1
                    if got["ok"] % 50 == 0:
                        rate = got["ok"] / (time.monotonic() - t0) * 60
                        print(f"[yt-panel] {got['ok']} ok ({rate:.0f}/min)", file=sys.stderr)
                elif status == "throttle":
                    print(f"[yt-panel] HTTP {detail} on {vid}: throttled — stopping to protect "
                          f"the account. Progress saved.", file=sys.stderr)
                    stop.set()
                else:
                    store.record(vid, status, error=str(detail))

        try:
            await asyncio.gather(*(worker() for _ in range(workers)))
        finally:
            await ctx.close()
            await browser.close()
    print(f"[yt-panel] done: {got['ok']} ok of {len(ids)} in {time.monotonic()-t0:.0f}s. "
          f"state={store.counts()}", file=sys.stderr)


def run(store: Store, ids: list[str], *, workers: int, out: Path) -> None:
    asyncio.run(_run_async(store, ids, max(1, workers), out))
