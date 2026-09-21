# crawl-telegram

Downloads Telegram chats and channels you have access to, via [Telethon](https://docs.telethon.dev/), into one folder per chat.

```bash
uv sync
cp .env.example .env         # TG_API_ID / TG_API_HASH from https://my.telegram.org
uv run crawl-telegram fetch @some_channel --from 2026-08-01
uv run crawl-telegram fetch "team chat" --pick            # private group without @handle: browse your dialogs
uv run crawl-telegram update all                           # append new messages to every fetched chat
uv run crawl-telegram upgrade some-channel --media photos  # backfill attachments later
```

The first run logs in interactively (phone, code, 2FA) and stores a session file named by `TG_SESSION` in the working directory. Session files and `.env` are gitignored — never commit them.

## What you get

`data/<slug>/`:

- `meta.json` — chat id, title, kind (`user | group | channel | forum`), member count, `extra.platform = "telegram"`
- `messages.jsonl` — one message per line: `id, date, sender_id, sender_name, sender_username, text, reply_to, topic_id, post_id, media_type, media_path, media_meta, mentions`
- `media/` — downloaded files (only with `--with-photos` / `upgrade`)

Broadcast channels are fetched together with their discussion threads; a comment carries the `post_id` of the post it belongs to. Polls keep question/options/voters, stickers their pack, audio its duration, contacts and geo their payload. `@mentions` are resolved to real users when possible (`--no-mentions` skips that and is much faster on big chats).

## Commands

| Command | Purpose |
|---|---|
| `fetch QUERY [--from D] [--to D] [--limit N] [--with-photos] [--pick] [-y]` | full download; replaces an earlier fetch of the same chat |
| `update all\|SLUG... [--with-photos]` | append messages newer than the last stored id |
| `upgrade SLUG... --media photos\|audio\|files\|all` | download attachments that were stored as placeholders |
| `reresolve all\|SLUG...` | retry `@mention` resolution after a FloodWait |

A proxy is picked up from `TG_PROXY` (or `ALL_PROXY` / `HTTPS_PROXY`): `socks5://host:port` or `http://user:pass@host:port`.
