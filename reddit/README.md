# crawl-reddit

Harvests subreddits into SQLite + JSONL. Three drivers, from zero-setup to full depth:

| Driver | Command | Needs | Gives |
|---|---|---|---|
| Atom feeds | `rss SUB...` | nothing | ~25 newest submissions per listing (new/top/hot), no comments |
| [Arctic Shift](https://arctic-shift.photon-reddit.com) archive | `archive SUB...` | nothing | **full history**: every post and comment, paginated by time |
| Reddit OAuth API | `crawl SUB... [-q QUERY]...` | free "script" app | live listings, in-subreddit search, comment trees |

```bash
uv sync
uv run crawl-reddit rss Aphantasia LucidDreaming
uv run crawl-reddit archive Aphantasia CureAphantasia          # posts + comments
uv run crawl-reddit archive leetcode --no-comments             # posts only (10x lighter)
uv run crawl-reddit crawl Aphantasia -q "mind's eye" -q cured  # needs .env
uv run crawl-reddit stats
uv run crawl-reddit youtube-links > yt_ids.txt                 # for crawl-youtube
```

## OAuth setup (only for `crawl`)

Reddit no longer serves its JSON API anonymously. Create a **script** app at <https://www.reddit.com/prefs/apps>, then `cp .env.example .env` and fill `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, and a unique `REDDIT_USER_AGENT` (Reddit blocks generic ones).

## Output

- `data/documents.jsonl` — one `Document` per line: `fullname` (`t3_…` post / `t1_…` comment), `kind`, `subreddit`, `author`, `title`, `text`, `permalink`, `created_utc`, `score`, `link_id`, `external_urls`, `extra`
- `data/crawl.sqlite3` — `items` (dedup by fullname), `links` (every URL found, YouTube ids extracted), `crawl_log`
- `data/raw/` — verbatim API payloads (`crawl` only)

Re-runs upsert by fullname and never duplicate. Ctrl-C is safe.

## Pacing

`REDDIT_DELAY` (default 2.5 s + jitter against reddit.com) and `ARCHIVE_DELAY` (default 1.2 s against the archive mirror) can be set in the environment. 429/5xx are retried with exponential backoff honouring `Retry-After`.
