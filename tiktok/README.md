# crawl-tiktok

Downloads everything public on one TikTok profile using the web app's own endpoints (no API key): post metadata and stats, comments with replies, and the images of photo/slideshow posts.

```bash
uv sync
uv run crawl-tiktok profile @someuser
uv run crawl-tiktok profile someuser --photos-only --with-images
uv run crawl-tiktok profile someuser --no-comments --max 100
uv run crawl-tiktok stats someuser
```

## Output

`data/tiktok/<user>/`:

- `posts.jsonl` — one post per line: `post_id, url, desc, create_time, is_photo_post, like_count, share_count, collect_count, comment_count, play_count, tags, image_urls, comments[{cid, user, text, like_count, reply_count, replies[...]}]`
- `<post_id>/images/00.jpg …` — with `--with-images`
- `progress.json` — `ok | skip:… | err:…` per post, so re-runs resume (`--retry-errors` retries failures)

## Blocking

TikTok serves a short CAPTCHA page instead of content when it dislikes an IP, and datacenter IPs get it almost immediately. The tool detects that by response size, pauses and records the post as `err:` for a later retry. Running through your own network or a residential egress (`--proxy http://127.0.0.1:PORT`, or `TIKTOK_PROXY` in `.env`) is usually required for anything beyond a handful of posts. `--delay` (default 1 s) spaces requests.
