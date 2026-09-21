# crawl-tiktok

Downloads everything public on one TikTok profile: post metadata and stats, comments with replies, and the images of photo/slideshow posts. No API key.

```bash
uv sync && uv run playwright install chromium
uv run crawl-tiktok profile @someuser
uv run crawl-tiktok profile someuser --photos-only --with-images
uv run crawl-tiktok profile someuser --no-comments --max 100
uv run crawl-tiktok stats someuser
```

## How it works (and why a browser window opens)

TikTok fingerprints the TLS stack and signs its feed requests, so plain HTTP clients get an empty body or a CAPTCHA page no matter which cookies they carry. The default `--driver browser` therefore drives a real Chromium (Playwright, persistent profile under `data/tiktok/browser_profile`):

- the post list is captured from the responses the profile page itself makes while the crawler scrolls it, so every post's description, stats, tags and image URLs come for free;
- comments and replies are fetched with `fetch()` from inside the page;
- when TikTok shows a CAPTCHA, **you solve it in the window** and the crawler continues (it waits up to `--human-wait` seconds, default 300).

`--driver requests` is the old plain-HTTP path; it only works from an IP TikTok already trusts (`--proxy` / `TIKTOK_PROXY`) and is kept for completeness.

## Output

`data/tiktok/<user>/`:

- `posts.jsonl` — one post per line: `post_id, url, desc, create_time, is_photo_post, like_count, share_count, collect_count, comment_count, play_count, tags, image_urls, comments[{cid, user, text, like_count, reply_count, create_time, replies[...]}]`
- `<post_id>/images/00.jpg …` — with `--with-images`
- `progress.json` — `ok | skip:… | err:…` per post; re-runs resume, `--retry-errors` retries failures

## Options

| Flag | Meaning |
|---|---|
| `--max N` | stop after N newly processed posts |
| `--no-comments`, `--no-replies` | skip comments / reply threads (replies are the slow part) |
| `--photos-only` | skip video-only posts |
| `--with-images` | download slideshow images |
| `--delay S` | pause between requests (default 1 s) |
| `--headless` | no window; a CAPTCHA then aborts the run instead of waiting for you |
| `--proxy URL` | route the browser through a proxy |
