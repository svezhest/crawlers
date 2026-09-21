# crawlers

Command-line harvesters for four sources, one folder each, sharing the same conventions:

| Folder | Command | What it downloads | Auth |
|---|---|---|---|
| [`youtube/`](youtube/) | `crawl-youtube` | Video transcripts (text + JSONL) | none (optional: your own YouTube login for the fast path) |
| [`reddit/`](reddit/) | `crawl-reddit` | Subreddit submissions + comments | none for `rss`/`archive`; free OAuth app for `crawl` |
| [`telegram/`](telegram/) | `crawl-telegram` | Chats and channels incl. comment threads, polls, media | Telegram API id/hash (my.telegram.org) |
| [`tiktok/`](tiktok/) | `crawl-tiktok` | Profile posts, comments with replies, photo-post images | none (a Chromium window opens; you solve TikTok's CAPTCHA by hand) |

Every tool is:

- **resumable** — state lives next to the data; re-running never duplicates and picks up where it stopped;
- **polite** — jittered delays, exponential backoff, and a hard stop on the first sign of a block;
- **JSONL-first** — one document per line, ready for grep, pandas, or an LLM pipeline;
- **credential-free in git** — secrets come from `./.env` (see each `.env.example`); `.env`, sessions, browser profiles and `data/` are gitignored.

## Quick start

Each folder is an independent [uv](https://docs.astral.sh/uv/) project.

```bash
cd youtube && uv sync && uv run crawl-youtube --help
cd reddit   && uv sync && uv run crawl-reddit --help
cd telegram && uv sync && cp .env.example .env && uv run crawl-telegram --help
cd tiktok   && uv sync && uv run crawl-tiktok --help
```

Or install a tool globally: `uv tool install ./youtube` gives you `crawl-youtube` on PATH.

## Chaining sources

Reddit posts link to YouTube constantly; the Reddit tool records every link and the YouTube tool eats the list:

```bash
cd reddit && uv run crawl-reddit archive Aphantasia --no-comments
uv run crawl-reddit youtube-links > ../youtube/ids.txt
cd ../youtube && uv run crawl-youtube fetch --file ids.txt --max 40
```

## Output layout

All tools write under `./data` by default (override with `--out`):

```
data/
  transcripts/<video_id>.txt, transcripts.jsonl, state.sqlite3     # youtube
  documents.jsonl, crawl.sqlite3, raw/                             # reddit
  <chat-slug>/{meta.json, messages.jsonl, media/}                  # telegram
  tiktok/<user>/{posts.jsonl, progress.json, <post_id>/images/}    # tiktok
```

## Output formats are not unified (yet)

Each tool keeps the native schema of its source, documented in its own README:

| Source | Text | Author | Time | Parent |
|---|---|---|---|---|
| youtube | `text` | — | `fetched_utc` | — |
| reddit | `text` (+`title`) | `author` | `created_utc` | `link_id` / `extra.parent_id` |
| telegram | `text` | `sender_name` / `sender_id` | `date` (ISO) | `reply_to` / `post_id` |
| tiktok | `desc`, comments `text` | `username`, comments `user` | `create_time` | nested `comments[].replies[]` |

Planned: an `export` subcommand in every tool that writes a common envelope
`{id, source, url, author, title, text, created_at, parent_id, extra}` to a single
JSONL, so one downstream pipeline can read all four sources. Native files stay as they
are; the envelope is a projection. Not started.

## Responsible use

These tools read publicly available content (or, for Telegram, chats your own account can already see) using the platforms' own web endpoints. Respect each platform's terms, rate limits and the privacy of the people whose words you download. Nothing here bypasses paywalls, logins you don't own, or CAPTCHAs.

## License

MIT
