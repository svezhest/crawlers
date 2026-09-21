# crawl-youtube

Downloads YouTube transcripts and keeps per-video state so nothing is fetched twice.

```bash
uv sync
uv run crawl-youtube fetch https://youtu.be/dQw4w9WgXcQ
uv run crawl-youtube fetch --file ids.txt --langs ru,en --max 40
uv run crawl-youtube stats
```

`ids.txt`: one URL or 11-char id per line (tab-separated extra columns and `#` comments are ignored, so `crawl-reddit youtube-links` output works as is).

## Two drivers

| Driver | Command | Speed | Notes |
|---|---|---|---|
| Anonymous API (`youtube_transcript_api`) | `fetch` | slow (≈ 20–30 s per video by default) | YouTube throttles the anonymous caption endpoint **per IP**. The tool spaces requests, backs off on a block and stops the run rather than hammering. `--proxy-ports 20001,20002` rotates across local SOCKS5 exits and rests a blocked exit. |
| Logged-in panel (`youtubei/v1/get_panel`) | `login`, then `panel` | fast (hundreds/min) | Uses **your own** YouTube account through a persistent Chromium profile (Playwright). Account-bound rather than IP-bound; stops instantly on 401/403/429. Install with `uv sync --extra browser && uv run playwright install chromium`. |

```bash
uv sync --extra browser && uv run playwright install chromium
uv run crawl-youtube login                       # sign in once; profile saved under data/browser_profile (gitignored)
uv run crawl-youtube panel --file ids.txt --workers 20
```

## Output

- `data/transcripts/<id>.txt` — plain text, one cue per line
- `data/transcripts.jsonl` — `{"id","source":"youtube","video_id","lang","url","text","fetched_utc"}`
- `data/state.sqlite3` — status per id: `ok | disabled | none | unavailable | invalid` (terminal), `blocked | error` (retry with `--retry`)

## Config

`.env` in the working directory (optional): `YT_DELAY`, `YT_JITTER`, `YT_PROXY_PORTS`. The Innertube client version used by the panel driver can be pinned with `YT_CLIENT_VER` if YouTube starts answering 400.
