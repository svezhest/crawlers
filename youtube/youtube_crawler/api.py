"""Driver 1: `youtube_transcript_api` (anonymous). Simple, but YouTube rate-limits it by
IP — hard. Hence: long jittered spacing, a per-run cap, optional rotation across local
SOCKS exits, and a full stop on the first block instead of hammering.
"""
from __future__ import annotations

import random
import sys
import time

import requests
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api import _errors as err

from .store import Store

_BLOCK_NAMES = {"SSLError", "ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout",
                "ProxyError", "ChunkedEncodingError", "MaxRetryError", "YouTubeRequestFailed"}


def _api(port: int | None, timeout: float) -> YouTubeTranscriptApi:
    class _S(requests.Session):
        def request(self, *a, **kw):
            kw.setdefault("timeout", timeout)
            return super().request(*a, **kw)
    s = _S()
    if port is not None:
        s.proxies = {"http": f"socks5h://127.0.0.1:{port}", "https": f"socks5h://127.0.0.1:{port}"}
    return YouTubeTranscriptApi(http_client=s)


def _is_block(e: BaseException) -> bool:
    return isinstance(e, (err.RequestBlocked, err.IpBlocked)) or type(e).__name__ in _BLOCK_NAMES


def fetch_one(vid: str, langs: list[str], port: int | None, timeout: float) -> tuple[str, object]:
    """Returns ("ok", (text, lang)) or (status, detail)."""
    try:
        t = _api(port, timeout).fetch(vid, languages=langs)
        text = "\n".join(e.text for e in t)
        return "ok", (text, getattr(t, "language_code", "") or "")
    except err.TranscriptsDisabled as e:
        return "disabled", str(e)
    except err.NoTranscriptFound as e:
        return "none", str(e)
    except (err.VideoUnavailable, err.VideoUnplayable, err.AgeRestricted) as e:
        return "unavailable", str(e)
    except err.InvalidVideoId as e:
        return "invalid", str(e)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:
        return ("blocked" if _is_block(e) else "error"), f"{type(e).__name__}: {e}"


def run(store: Store, ids: list[str], *, langs: list[str], delay: float, jitter: float,
        max_new: int | None, ports: list[int], timeout: float, block_backoff: float = 120.0,
        block_retries: int = 2) -> None:
    got = 0
    cooldown: dict[int, float] = {}  # port -> until
    for i, vid in enumerate(ids):
        if max_new is not None and got >= max_new:
            print(f"[yt] cap {max_new} reached; stopping.", file=sys.stderr)
            break
        tried: set[int] = set()
        attempt = 0
        while True:
            port = None
            if ports:
                avail = [p for p in ports if p not in tried and cooldown.get(p, 0) <= time.time()]
                if not avail:
                    nap = max(1.0, min(cooldown.values()) - time.time()) if cooldown else block_backoff
                    print(f"[yt] all exits cooling; sleeping {nap:.0f}s", file=sys.stderr)
                    time.sleep(nap)
                    tried.clear()
                    continue
                port = random.choice(avail)
                tried.add(port)
            status, detail = fetch_one(vid, langs, port, timeout)
            if status == "ok":
                text, lang = detail
                n = store.write_ok(vid, text, lang)
                got += 1
                print(f"[yt] ok {vid} ({n} chars, {lang or '?'}) [{i+1}/{len(ids)}]", file=sys.stderr)
                break
            if status == "blocked":
                if port is not None:
                    cooldown[port] = time.time() + block_backoff
                    print(f"[yt] exit :{port} blocked on {vid}; rotating", file=sys.stderr)
                    if len(tried) < len(ports):
                        continue
                    store.record(vid, "blocked", error=str(detail))
                    break
                attempt += 1
                if attempt > block_retries:
                    store.record(vid, "blocked", error=str(detail))
                    print(f"[yt] IP appears blocked — stopping the run. Retry later "
                          f"(progress is saved).", file=sys.stderr)
                    return
                wait = block_backoff * (2 ** (attempt - 1))
                print(f"[yt] blocked on {vid}; backing off {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            store.record(vid, status, error=str(detail))
            print(f"[yt] {status} {vid}: {str(detail)[:80]}", file=sys.stderr)
            break
        if not ports:
            time.sleep(delay + random.uniform(0, jitter))
    print(f"[yt] done: {got} new. state={store.counts()}", file=sys.stderr)
